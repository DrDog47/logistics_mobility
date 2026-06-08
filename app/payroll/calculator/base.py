"""Common types and dispatch for payroll calculators.

The calculator version is stored on every PayrollPeriod when calculated.
Bump it whenever the calc logic changes — this marks results for audit.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.drivers.models import ContractType, DriverContract

# Phase 2: added virtual diet, sanitariaty, ZUS/PIT bases, NBP integration.
CALCULATOR_VERSION = "phase2-2026-06"


class CalculatorError(RuntimeError):
    """Raised when a payroll period cannot be calculated."""


@dataclass(frozen=True, slots=True)
class CountryAggregate:
    """Sum of work hours for one (country, rate_name) bucket within a period."""

    country: str
    rate_name: str
    posted_hours: Decimal


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last day of the given month."""
    first = date(year, month, 1)
    _, last_day = monthrange(year, month)
    last = date(year, month, last_day)
    return first, last


def active_contract(driver, on_date: date) -> DriverContract:
    """Return the contract active on `on_date` for the driver."""
    for contract in driver.contracts:
        if contract.start_date <= on_date and (
            contract.end_date is None or contract.end_date >= on_date
        ):
            return contract
    raise CalculatorError(
        f"No active contract for {driver.full_name} on {on_date.isoformat()}"
    )


def calculate(period) -> None:
    """Dispatch to the correct calculator based on the driver's contract type."""
    _, period_end = month_bounds(period.year, period.month)
    contract = active_contract(period.driver, period_end)

    if contract.contract_type == ContractType.UMOWA_O_PRACE:
        from app.payroll.calculator import umowa_pracy

        umowa_pracy.calculate(period, contract)
    elif contract.contract_type == ContractType.UMOWA_ZLECENIA:
        raise CalculatorError(
            "Umowa zlecenia calculation is implemented in Phase 5, not Phase 2."
        )
    elif contract.contract_type == ContractType.B2B:
        raise CalculatorError(
            "B2B drivers do not go through Mobility Package wage equalization. "
            "Phase 5 will add a simplified path that only tracks hours for IMI."
        )
    else:
        raise CalculatorError(f"Unknown contract type: {contract.contract_type}")
