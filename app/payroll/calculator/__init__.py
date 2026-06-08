"""Payroll calculation engine.

Architecture:
- `base.py` — shared types and the dispatch function `calculate(period)`
- One module per contract type: `umowa_pracy.py`, (later) `umowa_zlecenia.py`, `b2b.py`

All calculators take a PayrollPeriod, read segments from the DB, and produce
PayrollLine records. The DB writes happen at the end in a single transaction.
"""

from app.payroll.calculator.base import CALCULATOR_VERSION, calculate

__all__ = ["calculate", "CALCULATOR_VERSION"]
