"""Country rate snapshots persisted in the database.

When a payroll calculation runs, it captures a snapshot of WHICH rate
value from WHICH YAML period it used. This is what guarantees reproducibility
when YAML files are updated later and an old period is audited.

NEVER mutate a snapshot. They're append-only by convention; the only
acceptable change is hard-deletion when the related payroll period is voided.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class CountryRateSnapshot(db.Model):
    """Frozen record of a rate used in a specific payroll calculation."""

    __tablename__ = "country_rate_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)

    # What was looked up
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    rate_name: Mapped[str] = mapped_column(String(64), nullable=False)
    queried_for_date: Mapped[date] = mapped_column(Date, nullable=False)

    # What was found
    hourly: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    monthly_gross: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Source attribution — the YAML period the rate came from
    period_valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_verified_at: Mapped[date] = mapped_column(Date, nullable=False)
    period_verified_by: Mapped[str] = mapped_column(String(64), nullable=False)

    # When the snapshot was created (= when the payroll calc ran)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<CountryRateSnapshot {self.country}/{self.rate_name} "
            f"@ {self.queried_for_date} = {self.hourly} {self.currency}>"
        )
