"""Phase 2 calculator tests — virtual diets, sanitariaty, ZUS/PIT, net pay.

These exercise the math that distinguishes Phase 2 from Phase 1:
- Days-abroad computation (auto + override)
- Virtual diet ZUS threshold logic (only applies if gross > avg wage)
- Virtual diet ZUS floor protection (ZUS base never below avg wage)
- Virtual diet PIT (always applies, no threshold)
- Sanitariaty calculation (60 PLN × days)
- ZUS employee 13.71%, zdrowotne 9%, PIT 12% advance
- Net pay equation end-to-end
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.documents.constants import ENTITY_DRIVER
from app.documents.models import DocumentType, DriverDocument
from app.drivers.models import ContractType, Driver
from app.extensions import db
from app.payroll.calculator import calculate
from app.payroll.models import PayrollLineType, PayrollPeriod, PayrollStatus
from app.rates.services import init_registry
from app.tax.polish_params import init_polish_params
from app.trips.models import SegmentType, Trip, TripSegment, TripStatus
from app.vehicles.models import Vehicle, VehicleType


@pytest.fixture(autouse=True)
def _init_runtime_for_tests(app):
    init_registry(app)
    init_polish_params(app)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_setup(base_salary_pln: Decimal):
    driver = Driver(
        first_name="Test",
        last_name="Driver",
        nationality="POL",
        hire_date=date(2024, 1, 1),
    )
    db.session.add(driver)
    db.session.flush()
    if not db.session.execute(
        db.select(DocumentType).where(
            DocumentType.type == "employment", DocumentType.entity_type == ENTITY_DRIVER
        )
    ).scalar_one_or_none():
        db.session.add(DocumentType(type="employment", entity_type=ENTITY_DRIVER, label="Employment"))
        db.session.flush()
    db.session.add(
        DriverDocument(
            driver_uuid=driver.uuid,
            document_type="employment",
            start_date=date(2024, 1, 1),
            end_date=None,
            extra={
                "contract_type": ContractType.UMOWA_O_PRACE.value,
                "base_salary_pln": str(base_salary_pln),
                "hours_norm": 168,
            },
        )
    )
    vehicle = Vehicle(plate="TST 99999", vehicle_type=VehicleType.TRUCK)
    db.session.add(vehicle)
    db.session.flush()
    return driver, vehicle


def _add_trip(driver, vehicle, segments_data, *, year=2026, month=3) -> Trip:
    trip = Trip(
        driver_id=driver.id,
        vehicle_id=vehicle.id,
        trip_number=f"T-{year}-{month}",
        start_date=date(year, month, 1),
        end_date=date(year, month, 28),
        status=TripStatus.CONFIRMED,
    )
    db.session.add(trip)
    db.session.flush()
    for i, sd in enumerate(segments_data):
        db.session.add(
            TripSegment(
                trip_id=trip.id,
                sequence=i,
                work_date=sd["work_date"],
                country=sd["country"],
                segment_type=sd["segment_type"],
                work_hours=sd["work_hours"],
                rate_name=sd.get("rate_name", "driver_default"),
            )
        )
    db.session.flush()
    return trip


def _new_period(driver_id, year, month, eur_pln, *, days_override=None) -> PayrollPeriod:
    p = PayrollPeriod(
        driver_id=driver_id,
        year=year,
        month=month,
        eur_pln_rate=eur_pln,
        days_abroad_override=days_override,
        status=PayrollStatus.DRAFT,
    )
    db.session.add(p)
    db.session.flush()
    return p


def _line_amount(period, line_type):
    for l in period.lines:
        if l.line_type == line_type:
            return l.amount_pln
    return None


# ---------------------------------------------------------------------------
# Days abroad: auto-counted from posted-segment dates
# ---------------------------------------------------------------------------


def test_days_abroad_counts_distinct_non_pl_dates(app):
    with app.app_context():
        driver, vehicle = _make_setup(base_salary_pln=Decimal("5000.00"))
        # Same date twice in DE → counts as 1 day. PL segment → not counted.
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, 5), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("4")},
            {"work_date": date(2026, 3, 5), "country": "DE",
             "segment_type": SegmentType.CROSS_TRADE, "work_hours": Decimal("4")},
            {"work_date": date(2026, 3, 6), "country": "FR",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")},
            {"work_date": date(2026, 3, 7), "country": "PL",
             "segment_type": SegmentType.BILATERAL, "work_hours": Decimal("8")},
        ])

        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        # 2 distinct foreign dates (3/5 and 3/6)
        assert period.days_abroad_auto == 2


def test_days_abroad_override_takes_precedence(app):
    """If user provides days_abroad_override, calculator uses it for diets/sanitariaty."""
    with app.app_context():
        driver, vehicle = _make_setup(base_salary_pln=Decimal("5000.00"))
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, 10), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")},
        ])

        # Override: claim 15 days even though only 1 segment date
        period = _new_period(
            driver.id, 2026, 3, Decimal("4.2800"), days_override=15,
        )
        calculate(period)
        db.session.commit()

        assert period.days_abroad_auto == 1
        # Sanitariaty: 15 × 60 PLN = 900
        assert _line_amount(period, PayrollLineType.SANITARIATY) == Decimal("900.00")


# ---------------------------------------------------------------------------
# Sanitariaty (60 PLN × days)
# ---------------------------------------------------------------------------


def test_sanitariaty_per_day(app):
    with app.app_context():
        driver, vehicle = _make_setup(base_salary_pln=Decimal("5000.00"))
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, d), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")}
            for d in (5, 6, 7, 8, 9)
        ])
        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        # 5 days × 60 PLN = 300
        assert period.days_abroad_auto == 5
        assert period.sanitariaty_pln == Decimal("300.00")
        assert _line_amount(period, PayrollLineType.SANITARIATY) == Decimal("300.00")


# ---------------------------------------------------------------------------
# Virtual diet ZUS — threshold + floor logic
# ---------------------------------------------------------------------------


def test_virtual_diet_zus_NOT_applied_below_threshold(app):
    """If gross <= avg_wage (9420), no ZUS diet — base = gross."""
    with app.app_context():
        # Base 5000, posted hours small → gross stays at 5000 (below 9420)
        driver, vehicle = _make_setup(base_salary_pln=Decimal("5000.00"))
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, 5), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")},
        ])
        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        assert period.total_gross_pln == Decimal("5000.00")
        # ZUS base = gross (no diet applied)
        assert period.zus_base_pln == Decimal("5000.00")
        assert _line_amount(period, PayrollLineType.VIRTUAL_DIET_ZUS) is None


def test_virtual_diet_zus_applied_with_floor_protection(app):
    """If gross > avg_wage, ZUS diet applies but base can't fall below avg wage."""
    with app.app_context():
        # Heavy posting → gross >> 9420
        # 200h × 13.90 EUR × 4.28 = 11898.40 PLN
        driver, vehicle = _make_setup(base_salary_pln=Decimal("3000.00"))
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, d), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("10")}
            for d in range(5, 25)  # 20 days × 10h = 200h
        ])
        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        # gross = max(3000, 11898.40) = 11898.40
        # ZUS diet potential = 60 EUR × 20 days × 4.28 = 5136.00 PLN
        # ZUS base floor = 9420
        # Max deductible = gross - floor = 11898.40 - 9420 = 2478.40
        # Diet applied = min(5136.00, 2478.40) = 2478.40
        # ZUS base = gross - diet = 11898.40 - 2478.40 = 9420.00 (= floor!)
        assert period.total_gross_pln == Decimal("11898.40")
        assert period.days_abroad_auto == 20
        diet_zus = _line_amount(period, PayrollLineType.VIRTUAL_DIET_ZUS)
        assert diet_zus == Decimal("2478.40")
        assert period.zus_base_pln == Decimal("9420.00")  # floor exactly


def test_virtual_diet_zus_full_when_gross_far_above_floor(app):
    """If gross − full_diet >= avg_wage, the FULL diet applies (not capped)."""
    with app.app_context():
        # Huge salary: contract base 20000 PLN; 1 day abroad
        driver, vehicle = _make_setup(base_salary_pln=Decimal("20000.00"))
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, 5), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")},
        ])
        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        # gross = max(20000, 8 × 13.90 × 4.28 = 475.94) = 20000
        # ZUS diet = 60 × 1 × 4.28 = 256.80
        # Max deductible = 20000 - 9420 = 10580 (much > 256.80)
        # Diet applied = 256.80 (full)
        # ZUS base = 20000 - 256.80 = 19743.20
        assert _line_amount(period, PayrollLineType.VIRTUAL_DIET_ZUS) == Decimal("256.80")
        assert period.zus_base_pln == Decimal("19743.20")


# ---------------------------------------------------------------------------
# Virtual diet PIT — no threshold
# ---------------------------------------------------------------------------


def test_virtual_diet_pit_applies_from_day_one(app):
    """Even at low gross, PIT diet still reduces PIT base."""
    with app.app_context():
        driver, vehicle = _make_setup(base_salary_pln=Decimal("4000.00"))
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, 5), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")},
            {"work_date": date(2026, 3, 6), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")},
        ])
        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        # PIT diet: 20 EUR × 2 days × 4.28 = 171.20 PLN
        diet_pit = _line_amount(period, PayrollLineType.VIRTUAL_DIET_PIT)
        assert diet_pit == Decimal("171.20")
        # PIT base = gross - PIT diet = 4000 - 171.20 = 3828.80
        assert period.pit_base_pln == Decimal("3828.80")
        # ZUS base = gross (gross < 9420, no ZUS diet)
        assert period.zus_base_pln == Decimal("4000.00")


# ---------------------------------------------------------------------------
# ZUS employee + zdrowotne + PIT advance + net pay
# ---------------------------------------------------------------------------


def test_full_net_pay_pipeline_simple_case(app):
    """End-to-end: 5000 base, no posting → standard Polish payslip math."""
    with app.app_context():
        driver, vehicle = _make_setup(base_salary_pln=Decimal("5000.00"))
        # No trips — pure base salary case
        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        gross = Decimal("5000.00")
        assert period.total_gross_pln == gross
        assert period.zus_base_pln == gross
        assert period.pit_base_pln == gross

        # ZUS employee social: 13.71% × 5000 = 685.50
        zus_emp = _line_amount(period, PayrollLineType.ZUS_EMPLOYEE)
        assert zus_emp == Decimal("685.50")
        assert period.zus_employee_pln == zus_emp

        # Zdrowotne: 9% × (5000 - 685.50) = 9% × 4314.50 = 388.305 → 388.31
        zdr = _line_amount(period, PayrollLineType.ZDROWOTNE)
        assert zdr == Decimal("388.31")

        # PIT: pit_base (5000) - 250 koszty = 4750
        # tax = 4750 × 12% = 570 - 300 (kwota wolna) = 270
        pit = _line_amount(period, PayrollLineType.PIT_ADVANCE)
        assert pit == Decimal("270.00")

        # Net = gross - zus - zdrowotne - pit + sanitariaty(0)
        # = 5000 - 685.50 - 388.31 - 270 = 3656.19
        assert period.total_net_pln == Decimal("3656.19")


def test_recalculation_replaces_prior_lines(app):
    """Calling calculate() twice doesn't double-up — lines are cleared first."""
    with app.app_context():
        driver, vehicle = _make_setup(base_salary_pln=Decimal("5000.00"))
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, 5), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")},
        ])
        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()
        lines_first = len(period.lines)

        # Re-run
        calculate(period)
        db.session.commit()
        lines_second = len(period.lines)

        assert lines_first == lines_second
        assert period.total_net_pln is not None


# ---------------------------------------------------------------------------
# Snapshot integrity — Phase 2 still produces snapshots like Phase 1
# ---------------------------------------------------------------------------


def test_foreign_wage_lines_carry_snapshots(app):
    with app.app_context():
        driver, vehicle = _make_setup(base_salary_pln=Decimal("3000.00"))
        _add_trip(driver, vehicle, [
            {"work_date": date(2026, 3, 5), "country": "DE",
             "segment_type": SegmentType.CABOTAGE, "work_hours": Decimal("8")},
            {"work_date": date(2026, 3, 6), "country": "FR",
             "segment_type": SegmentType.CROSS_TRADE, "work_hours": Decimal("8"),
             "rate_name": "driver_coef_150m"},
        ])
        period = _new_period(driver.id, 2026, 3, Decimal("4.2800"))
        calculate(period)
        db.session.commit()

        foreign_lines = [l for l in period.lines if l.line_type == PayrollLineType.FOREIGN_WAGE]
        assert len(foreign_lines) == 2
        for line in foreign_lines:
            assert line.snapshot_id is not None
            assert line.snapshot.period_verified_at is not None
