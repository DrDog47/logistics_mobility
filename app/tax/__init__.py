"""Polish payroll tax/contribution parameters.

Each year's parameters live in data/tax_rules/pl_YYYY.yaml. Loaded at startup
and accessible via `get_polish_params(year)`.
"""

from app.tax.polish_params import (
    PolishParams,
    get_polish_params,
    init_polish_params,
)

__all__ = ["PolishParams", "get_polish_params", "init_polish_params"]
