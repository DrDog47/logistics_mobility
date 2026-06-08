"""Loader for Polish year-by-year payroll parameters."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from flask import current_app

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed parameter container
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolishParams:
    """All Polish payroll parameters for one year, validated and frozen."""

    year: int
    average_wage_monthly: Decimal
    minimum_wage_monthly: Decimal

    zus_diet_eur_per_day: Decimal
    zus_diet_threshold_monthly: Decimal
    zus_diet_floor_monthly: Decimal
    pit_diet_eur_per_day: Decimal

    sanitariaty_pln_per_day: Decimal

    # ZUS employee rates (percentages)
    zus_emerytalne_pct: Decimal
    zus_rentowe_pct: Decimal
    zus_chorobowe_pct: Decimal

    zdrowotne_pct: Decimal

    # PIT (simplified)
    pit_bracket_1_threshold: Decimal
    pit_bracket_1_rate_pct: Decimal
    pit_bracket_2_rate_pct: Decimal
    pit_monthly_tax_reduction: Decimal
    pit_monthly_employee_costs: Decimal

    @property
    def zus_employee_total_pct(self) -> Decimal:
        return self.zus_emerytalne_pct + self.zus_rentowe_pct + self.zus_chorobowe_pct


# ---------------------------------------------------------------------------
# Registry — loaded once at startup
# ---------------------------------------------------------------------------


class PolishParamsError(RuntimeError):
    """Raised when loading or accessing Polish tax params fails."""


def _parse_one(path: Path) -> PolishParams:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PolishParamsError(f"{path.name}: root must be a mapping")

    try:
        idr = raw["international_driver"]
        zus = raw["zus_employee"]
        pit = raw["pit"]
        return PolishParams(
            year=int(raw["year"]),
            average_wage_monthly=Decimal(str(raw["average_wage_pln_monthly"])),
            minimum_wage_monthly=Decimal(str(raw["minimum_wage_pln_monthly"])),
            zus_diet_eur_per_day=Decimal(str(idr["zus_diet_eur_per_day"])),
            zus_diet_threshold_monthly=Decimal(str(idr["zus_diet_threshold_pln_monthly"])),
            zus_diet_floor_monthly=Decimal(str(idr["zus_diet_floor_pln_monthly"])),
            pit_diet_eur_per_day=Decimal(str(idr["pit_diet_eur_per_day"])),
            sanitariaty_pln_per_day=Decimal(str(raw["sanitariaty"]["pln_per_day_abroad"])),
            zus_emerytalne_pct=Decimal(str(zus["emerytalne_pct"])),
            zus_rentowe_pct=Decimal(str(zus["rentowe_pct"])),
            zus_chorobowe_pct=Decimal(str(zus["chorobowe_pct"])),
            zdrowotne_pct=Decimal(str(raw["zdrowotne"]["rate_pct"])),
            pit_bracket_1_threshold=Decimal(str(pit["bracket_1_threshold_pln_annual"])),
            pit_bracket_1_rate_pct=Decimal(str(pit["bracket_1_rate_pct"])),
            pit_bracket_2_rate_pct=Decimal(str(pit["bracket_2_rate_pct"])),
            pit_monthly_tax_reduction=Decimal(str(pit["monthly_tax_reduction_pln"])),
            pit_monthly_employee_costs=Decimal(str(pit["monthly_employee_costs_pln"])),
        )
    except KeyError as exc:
        raise PolishParamsError(f"{path.name}: missing key {exc}") from exc
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise PolishParamsError(f"{path.name}: invalid value: {exc}") from exc


def init_polish_params(app: Flask) -> None:
    """Load all pl_YYYY.yaml files at startup, attach to the app."""
    tax_dir: Path = app.config["TAX_RULES_DIR"]
    if not tax_dir.is_dir():
        raise PolishParamsError(f"Tax rules directory missing: {tax_dir}")

    params_by_year: dict[int, PolishParams] = {}
    for path in sorted(tax_dir.glob("pl_*.yaml")):
        p = _parse_one(path)
        params_by_year[p.year] = p
        logger.info("Loaded Polish params for %d (avg wage %s)", p.year, p.average_wage_monthly)

    if not params_by_year:
        raise PolishParamsError(f"No pl_YYYY.yaml files found in {tax_dir}")

    app.extensions["polish_params"] = params_by_year


def get_polish_params(year: int) -> PolishParams:
    """Look up parameters for a given calendar year."""
    params: dict[int, PolishParams] = current_app.extensions.get("polish_params", {})
    if year not in params:
        available = ", ".join(str(y) for y in sorted(params.keys()))
        raise PolishParamsError(
            f"No Polish tax parameters loaded for year {year}. Available: {available}"
        )
    return params[year]
