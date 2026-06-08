"""Cached NBP exchange rates.

Each row is one (currency, date) → rate triple. Populated from the NBP API
on demand, then re-used for all subsequent lookups. This keeps payroll
calculations reproducible — once a rate is in the DB for a date, recalcs
get the same number even if NBP retroactively corrects something.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class NbpRate(db.Model):
    """One NBP table-A rate, cached locally."""

    __tablename__ = "nbp_rates"
    __table_args__ = (
        UniqueConstraint("currency", "effective_date", name="uq_nbp_currency_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rate_pln: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    table_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<NbpRate {self.currency} {self.effective_date}={self.rate_pln}>"
