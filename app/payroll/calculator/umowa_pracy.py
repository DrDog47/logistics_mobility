"""Payroll calculator for umowa o pracę — Phase 2 complete.

Pipeline:
  1. Aggregate posted hours (CABOTAGE + CROSS_TRADE) per (country, rate_name)
  2. Per group: lookup foreign sector rate (snapshot) -> FOREIGN_WAGE line
  3. BASE_SALARY line from contract
  4. EQUALIZATION if foreign_sum > base
  5. Compute days_abroad (auto or override)
  6. SANITARIATY line (paid to driver, ZUS/PIT exempt)
  7. VIRTUAL_DIET_ZUS line — informational, reduces ZUS base (with threshold)
  8. VIRTUAL_DIET_PIT line — informational, reduces PIT base (no threshold)
  9. ZUS_EMPLOYEE = zus_base × 13.71%
 10. ZDROWOTNE = (gross - zus_employee) × 9%
 11. PIT_ADVANCE = max(0, pit_base × 12% − monthly_tax_reduction) − employee_costs_credit
 12. NET = gross - zus_employee - zdrowotne - pit_advance + sanitariaty

Notes:
- All monetary values stored as Decimal, rounded half-up to 2 places at line
  boundaries.
- Virtual diets are PURELY informational on the gross/net total — they only
  reduce the bases on which contributions are computed.
- Sanitariaty IS paid to the driver but exempt from ZUS/PIT.
- PIT advance is simplified: single 12% bracket; bracket-2 (32%) is deferred
  to a future "annual reconciliation" view since it depends on cumulative
  income from start of year.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from flask import current_app

from app.extensions import db
from app.payroll.calculator.base import (
    CALCULATOR_VERSION,
    CalculatorError,
    CountryAggregate,
    month_bounds,
)
from app.payroll.models import PayrollLine, PayrollLineType, PayrollPeriod, PayrollStatus
from app.rates.services import resolve_rate_with_snapshot
from app.tax.polish_params import PolishParams, get_polish_params
from app.trips.models import Trip, TripSegment, TripStatus

if TYPE_CHECKING:
    from app.drivers.models import DriverContract

TWO = Decimal("0.01")
HUNDRED = Decimal("100")


def _r(value: Decimal) -> Decimal:
    """Round half-up to 2 decimal places."""
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def calculate(period: PayrollPeriod, contract: "DriverContract") -> None:
    """Compute & persist payroll lines + period totals.

    Caller is responsible for db.session.commit().
    """
    if period.eur_pln_rate is None or period.eur_pln_rate <= 0:
        raise CalculatorError("EUR/PLN exchange rate must be set on the period.")

    # Clear prior calculation (re-run safe)
    for old in list(period.lines):
        db.session.delete(old)
    period.lines.clear()

    params = get_polish_params(period.year)
    period_start, period_end = month_bounds(period.year, period.month)
    fx = Decimal(period.eur_pln_rate)

    # ====================================================================
    # 1. Posted segments → foreign wage lines
    # ====================================================================

    aggregates = _aggregate_posted_segments(
        driver_id=period.driver_id,
        period_start=period_start,
        period_end=period_end,
    )

    foreign_total_pln = Decimal("0.00")
    for agg in aggregates:
        rate, snapshot = resolve_rate_with_snapshot(
            country=agg.country,
            rate_name=agg.rate_name,
            on_date=period_end,
            persist=True,
        )
        if snapshot.currency != "EUR":
            raise CalculatorError(
                f"Phase 2 supports EUR only (got {snapshot.currency} for {agg.country}). "
                f"Multi-currency NBP lookups land in Phase 3+."
            )

        amount_native = agg.posted_hours * rate.hourly
        amount_pln = _r(amount_native * fx)
        foreign_total_pln += amount_pln

        db.session.add(
            PayrollLine(
                period_id=period.id,
                line_type=PayrollLineType.FOREIGN_WAGE,
                country=agg.country,
                hours=agg.posted_hours,
                rate_hourly_native=rate.hourly,
                rate_currency=snapshot.currency,
                amount_native=_r(amount_native),
                amount_pln=amount_pln,
                snapshot_id=snapshot.id,
                description=(
                    f"{agg.country} {agg.rate_name}: {agg.posted_hours}h × "
                    f"{rate.hourly} {snapshot.currency} × {fx} PLN/EUR"
                ),
            )
        )

    # ====================================================================
    # 2. Base salary
    # ====================================================================

    base_pln = _r(Decimal(contract.base_salary_pln))
    db.session.add(
        PayrollLine(
            period_id=period.id,
            line_type=PayrollLineType.BASE_SALARY,
            hours=Decimal(contract.hours_norm),
            rate_currency="PLN",
            amount_native=base_pln,
            amount_pln=base_pln,
            description=f"Base salary ({contract.hours_norm}h norm @ {contract.base_salary_pln} PLN)",
        )
    )

    # ====================================================================
    # 3. Equalization
    # ====================================================================

    if foreign_total_pln > base_pln:
        equalization = _r(foreign_total_pln - base_pln)
        db.session.add(
            PayrollLine(
                period_id=period.id,
                line_type=PayrollLineType.EQUALIZATION,
                amount_native=equalization,
                amount_pln=equalization,
                rate_currency="PLN",
                description=(
                    f"Wyrównanie do pensji sektorowej "
                    f"({foreign_total_pln} − {base_pln})"
                ),
            )
        )
        gross_pln = foreign_total_pln
    else:
        equalization = Decimal("0.00")
        gross_pln = base_pln

    # ====================================================================
    # 4. Days abroad (auto + override)
    # ====================================================================

    days_abroad_auto = _compute_days_abroad(
        driver_id=period.driver_id,
        period_start=period_start,
        period_end=period_end,
    )
    period.days_abroad_auto = days_abroad_auto
    days_abroad = (
        period.days_abroad_override
        if period.days_abroad_override is not None
        else days_abroad_auto
    )

    # ====================================================================
    # 5. Sanitariaty (paid, exempt)
    # ====================================================================

    sanitariaty_pln = _r(params.sanitariaty_pln_per_day * days_abroad)
    if sanitariaty_pln > 0:
        db.session.add(
            PayrollLine(
                period_id=period.id,
                line_type=PayrollLineType.SANITARIATY,
                amount_native=sanitariaty_pln,
                amount_pln=sanitariaty_pln,
                rate_currency="PLN",
                description=(
                    f"Sanitariaty: {days_abroad} day(s) abroad × "
                    f"{params.sanitariaty_pln_per_day} PLN (ZUS/PIT exempt)"
                ),
            )
        )

    # ====================================================================
    # 6. Virtual diets — reduce taxation bases (informational lines)
    # ====================================================================

    # ZUS diet: 60 EUR × days, but ONLY if monthly gross > average wage.
    zus_diet_pln = Decimal("0.00")
    if gross_pln > params.average_wage_monthly and days_abroad > 0:
        raw_zus_diet = _r(params.zus_diet_eur_per_day * days_abroad * fx)
        # ZUS base never falls below the "floor" (= average wage)
        max_deductible = max(Decimal("0.00"), gross_pln - params.zus_diet_floor_monthly)
        zus_diet_pln = min(raw_zus_diet, max_deductible)
        if zus_diet_pln > 0:
            db.session.add(
                PayrollLine(
                    period_id=period.id,
                    line_type=PayrollLineType.VIRTUAL_DIET_ZUS,
                    amount_native=zus_diet_pln,
                    amount_pln=zus_diet_pln,
                    rate_currency="PLN",
                    description=(
                        f"Wirtualna dieta ZUS: {days_abroad} days × "
                        f"{params.zus_diet_eur_per_day} EUR × {fx} "
                        f"(capped by floor {params.zus_diet_floor_monthly} PLN)"
                    ),
                )
            )

    # PIT diet: 20 EUR × days — no threshold, applies from day 1.
    pit_diet_pln = Decimal("0.00")
    if days_abroad > 0:
        pit_diet_pln = _r(params.pit_diet_eur_per_day * days_abroad * fx)
        # PIT diet capped at gross (can't go below zero base)
        pit_diet_pln = min(pit_diet_pln, gross_pln)
        if pit_diet_pln > 0:
            db.session.add(
                PayrollLine(
                    period_id=period.id,
                    line_type=PayrollLineType.VIRTUAL_DIET_PIT,
                    amount_native=pit_diet_pln,
                    amount_pln=pit_diet_pln,
                    rate_currency="PLN",
                    description=(
                        f"Wirtualna dieta PIT: {days_abroad} days × "
                        f"{params.pit_diet_eur_per_day} EUR × {fx}"
                    ),
                )
            )

    # ====================================================================
    # 7. Bases
    # ====================================================================

    zus_base = _r(gross_pln - zus_diet_pln)
    pit_base = _r(gross_pln - pit_diet_pln)

    # ====================================================================
    # 8. Employee contributions
    # ====================================================================

    zus_employee = _r(zus_base * params.zus_employee_total_pct / HUNDRED)
    db.session.add(
        PayrollLine(
            period_id=period.id,
            line_type=PayrollLineType.ZUS_EMPLOYEE,
            amount_native=zus_employee,
            amount_pln=zus_employee,
            rate_currency="PLN",
            description=(
                f"ZUS employee social ({params.zus_employee_total_pct}% × "
                f"{zus_base} PLN base)"
            ),
        )
    )

    # Zdrowotne: 9% of (gross - employee social ZUS).
    # Simplified — the official base also excludes some items but this is close.
    zdrowotne_base = gross_pln - zus_employee
    zdrowotne = _r(zdrowotne_base * params.zdrowotne_pct / HUNDRED)
    db.session.add(
        PayrollLine(
            period_id=period.id,
            line_type=PayrollLineType.ZDROWOTNE,
            amount_native=zdrowotne,
            amount_pln=zdrowotne,
            rate_currency="PLN",
            description=f"Health insurance ({params.zdrowotne_pct}% × {zdrowotne_base} PLN)",
        )
    )

    # PIT advance: 12% of (pit_base − employee_costs) − monthly_tax_reduction
    # Floored at 0. Annual bracket-2 reconciliation deferred.
    pit_taxable = max(Decimal("0.00"), pit_base - params.pit_monthly_employee_costs)
    pit_gross = pit_taxable * params.pit_bracket_1_rate_pct / HUNDRED
    pit_advance = max(Decimal("0.00"), _r(pit_gross - params.pit_monthly_tax_reduction))
    db.session.add(
        PayrollLine(
            period_id=period.id,
            line_type=PayrollLineType.PIT_ADVANCE,
            amount_native=pit_advance,
            amount_pln=pit_advance,
            rate_currency="PLN",
            description=(
                f"PIT advance ({params.pit_bracket_1_rate_pct}% × "
                f"{pit_taxable} − {params.pit_monthly_tax_reduction} reduction)"
            ),
        )
    )

    # ====================================================================
    # 9. Net pay & period totals
    # ====================================================================

    net_pln = _r(gross_pln - zus_employee - zdrowotne - pit_advance + sanitariaty_pln)

    period.foreign_wage_pln = _r(foreign_total_pln)
    period.equalization_pln = equalization
    period.total_gross_pln = _r(gross_pln)
    period.sanitariaty_pln = sanitariaty_pln
    period.zus_base_pln = zus_base
    period.pit_base_pln = pit_base
    period.zus_employee_pln = zus_employee
    period.zdrowotne_pln = zdrowotne
    period.pit_advance_pln = pit_advance
    period.total_net_pln = net_pln
    period.calculator_version = CALCULATOR_VERSION
    period.calculated_at = datetime.now(UTC)
    period.status = PayrollStatus.CALCULATED

    current_app.logger.info(
        "Calculated %s %d-%02d: gross=%s, zus_base=%s, pit_base=%s, net=%s "
        "(days_abroad=%d, sanitariaty=%s)",
        period.driver_id,
        period.year,
        period.month,
        period.total_gross_pln,
        zus_base,
        pit_base,
        net_pln,
        days_abroad,
        sanitariaty_pln,
    )


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _aggregate_posted_segments(
    *, driver_id: int, period_start, period_end
) -> list[CountryAggregate]:
    rows = (
        db.session.execute(
            db.select(TripSegment)
            .join(Trip, TripSegment.trip_id == Trip.id)
            .where(
                Trip.driver_id == driver_id,
                Trip.status == TripStatus.CONFIRMED,
                TripSegment.work_date >= period_start,
                TripSegment.work_date <= period_end,
            )
        )
        .scalars()
        .all()
    )

    buckets: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for seg in rows:
        if not seg.segment_type.is_posting:
            continue
        buckets[(seg.country, seg.rate_name)] += Decimal(seg.work_hours)

    return [
        CountryAggregate(country=country, rate_name=rate_name, posted_hours=hours)
        for (country, rate_name), hours in sorted(buckets.items())
    ]


def _compute_days_abroad(*, driver_id: int, period_start, period_end) -> int:
    """Distinct work_dates with ANY segment outside PL.

    Phase 2 approximation. Phase 3 (DDD parser) refines this to also capture
    rest days spent abroad, which the strict legal definition includes.
    """
    rows = (
        db.session.execute(
            db.select(TripSegment.work_date, TripSegment.country)
            .join(Trip, TripSegment.trip_id == Trip.id)
            .where(
                Trip.driver_id == driver_id,
                Trip.status == TripStatus.CONFIRMED,
                TripSegment.work_date >= period_start,
                TripSegment.work_date <= period_end,
            )
        )
        .all()
    )

    abroad_dates = {wd for wd, country in rows if country.upper() != "PL"}
    return len(abroad_dates)
