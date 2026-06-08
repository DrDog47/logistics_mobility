"""Polish payroll tax rules — values that change yearly.

THESE VALUES MUST BE REVIEWED EVERY YEAR. Sources:
- ZUS "Przeciętne wynagrodzenie" — published by GUS in December
- Sanitariaty per-day rate — Rozporządzenie MRiPS (currently 60 PLN)
- Virtual diet amounts (60 EUR ZUS, 20 EUR PIT) — Ustawa o czasie pracy
  kierowców art. 21b oraz Ustawa o PIT art. 21 ust. 1 pkt 20

When a new year is added, update PL_TAX_RULES below and bump
CALCULATOR_VERSION in payroll.calculator.base — this signals to anyone
reading audit logs that the rules have changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PlTaxRules:
    """Yearly Polish tax/diet rules relevant to international driver payroll."""

    year: int
    # Per-day sanitation allowance (PLN). Not subject to ZUS or PIT.
    sanitariaty_pln_per_day: Decimal
    # Virtual diet: amount per day abroad excluded from ZUS contribution base
    # (only when monthly gross > zus_diet_threshold_pln).
    virtual_diet_zus_eur_per_day: Decimal
    # Virtual diet: amount per day abroad exempt from PIT (income tax).
    virtual_diet_pit_eur_per_day: Decimal
    # Average monthly wage threshold (GUS "przeciętne wynagrodzenie").
    # ZUS virtual diet exemption kicks in only when gross >= this.
    zus_diet_threshold_pln: Decimal


PL_TAX_RULES: dict[int, PlTaxRules] = {
    2024: PlTaxRules(
        year=2024,
        sanitariaty_pln_per_day=Decimal("60.00"),
        virtual_diet_zus_eur_per_day=Decimal("60.00"),
        virtual_diet_pit_eur_per_day=Decimal("20.00"),
        zus_diet_threshold_pln=Decimal("7194.95"),  # GUS Q3 2023
    ),
    2025: PlTaxRules(
        year=2025,
        sanitariaty_pln_per_day=Decimal("60.00"),
        virtual_diet_zus_eur_per_day=Decimal("60.00"),
        virtual_diet_pit_eur_per_day=Decimal("20.00"),
        zus_diet_threshold_pln=Decimal("7824.00"),  # GUS Q3 2024
    ),
    2026: PlTaxRules(
        year=2026,
        sanitariaty_pln_per_day=Decimal("60.00"),
        virtual_diet_zus_eur_per_day=Decimal("60.00"),
        virtual_diet_pit_eur_per_day=Decimal("20.00"),
        zus_diet_threshold_pln=Decimal("8673.00"),  # GUS Q3 2025 — VERIFY
    ),
}


class TaxRulesNotDefined(RuntimeError):
    """Raised when calculation is requested for a year without configured rules."""


def get_tax_rules(year: int) -> PlTaxRules:
    rules = PL_TAX_RULES.get(year)
    if rules is None:
        raise TaxRulesNotDefined(
            f"Polish tax rules not defined for year {year}. "
            f"Update PL_TAX_RULES in app/services/pl_tax_rules.py."
        )
    return rules
