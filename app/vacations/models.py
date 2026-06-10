"""Vacation / leave models.

Three core tables plus a non-working-day catalogue and a single Google Calendar
account row:

- ``LeaveEntitlement`` — the annual-leave cap per (driver, year), with carry-over
  and a manual kadrowiec adjustment. ``entitled_days`` is the effective cap.
- ``LeaveEntry`` — one absence period (a leave). Caches ``counted_days`` (working
  or calendar days, per the kind's rule) so balances are a single ``SUM``.
- ``PublicHoliday`` — non-working days used by the working-day count. DB-backed so
  a company can add its own days; falls back to computed Polish holidays.
- ``GoogleCalendarAccount`` — OAuth token + target calendar for the (optional)
  sync. The app is the source of truth: approved leaves are pushed; external
  events are pulled in read-only.

There is deliberately **no approval workflow** — every entry is a fact and counts
immediately. Cancellation is a soft delete, not a status.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db_types import JsonB, PrdStandardMixin, UpdatedAtMixin, UuidType
from app.extensions import db


class LeaveKind(str, enum.Enum):
    """Types of absence. ``LEAVE_RULES`` carries the counting/limit behaviour."""

    ANNUAL = "annual"        # urlop wypoczynkowy — capped, counted in working days
    ON_DEMAND = "on_demand"  # urlop na żądanie — subset of annual, same cap
    SICK = "sick"            # L4 — uncapped, separate stat, calendar days
    UNPAID = "unpaid"        # urlop bezpłatny — uncapped, unpaid
    OTHER = "other"


class LeaveSource(str, enum.Enum):
    """Where a leave entry came from."""

    MANUAL = "manual"
    GOOGLE = "google_calendar"


# Per-kind rules. ``counts_against_limit`` → eats into the annual cap (drives the
# "remaining" calc). ``unit`` → how ``counted_days`` is measured: working days
# (Mon–Fri minus public holidays) or raw calendar days.
LEAVE_RULES: dict[LeaveKind, dict[str, object]] = {
    LeaveKind.ANNUAL: {"counts_against_limit": True, "unit": "working"},
    LeaveKind.ON_DEMAND: {"counts_against_limit": True, "unit": "working"},
    LeaveKind.SICK: {"counts_against_limit": False, "unit": "calendar"},
    LeaveKind.UNPAID: {"counts_against_limit": False, "unit": "working"},
    LeaveKind.OTHER: {"counts_against_limit": False, "unit": "working"},
}

# Kinds that consume the annual-leave allowance.
LIMIT_KINDS: tuple[LeaveKind, ...] = tuple(
    k for k, r in LEAVE_RULES.items() if r["counts_against_limit"]
)


def kind_unit(kind: LeaveKind) -> str:
    """The counting unit ('working' | 'calendar') for a leave kind."""
    return str(LEAVE_RULES[kind]["unit"])


def counts_against_limit(kind: LeaveKind) -> bool:
    """Whether a leave kind eats into the annual-leave allowance."""
    return bool(LEAVE_RULES[kind]["counts_against_limit"])


class LeaveEntitlement(PrdStandardMixin, db.Model):
    """Annual-leave allowance for one driver in one year."""

    __tablename__ = "leave_entitlements"

    driver_uuid: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("drivers.uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    base_days: Mapped[int] = mapped_column(Integer, nullable=False)       # 20 / 26
    carried_over_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    adjustment_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("driver_uuid", "year", name="uq_entitlement_driver_year"),
    )

    @property
    def entitled_days(self) -> int:
        """Effective cap = base + carried-over + manual adjustment."""
        return self.base_days + self.carried_over_days + self.adjustment_days

    def __repr__(self) -> str:
        return f"<LeaveEntitlement {self.driver_uuid} {self.year}={self.entitled_days}>"


class LeaveEntry(PrdStandardMixin, UpdatedAtMixin, db.Model):
    """A single absence period for a driver (inclusive of both end dates)."""

    __tablename__ = "leave_entries"

    driver_uuid: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("drivers.uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[LeaveKind] = mapped_column(Enum(LeaveKind), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Cached day count (working or calendar per the kind's rule). Recomputed on
    # save and on any change to the public-holiday catalogue for the year.
    counted_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Google Calendar sync (app is the source of truth) ---
    source: Mapped[LeaveSource] = mapped_column(
        Enum(LeaveSource), nullable=False, default=LeaveSource.MANUAL
    )
    google_calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_event_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    google_etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw: Mapped[dict | None] = mapped_column(JsonB, nullable=True)

    driver = relationship("Driver", backref="leave_entries")

    __table_args__ = (
        Index("ix_leave_driver_start", "driver_uuid", "start_date"),
    )

    @property
    def counts_against_limit(self) -> bool:
        return counts_against_limit(self.kind)

    def __repr__(self) -> str:
        return f"<LeaveEntry {self.kind.value} {self.start_date}..{self.end_date}>"


class PublicHoliday(PrdStandardMixin, db.Model):
    """A non-working day used by the working-day count.

    DB-backed so a company can add its own closure days; the calculation falls
    back to computed Polish statutory holidays when the table has none for a year
    (mirrors the document_type catalogue fallback).
    """

    __tablename__ = "public_holidays"

    country: Mapped[str] = mapped_column(String(2), nullable=False, default="PL")
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        UniqueConstraint("country", "day", name="uq_holiday_country_day"),
    )

    def __repr__(self) -> str:
        return f"<PublicHoliday {self.country} {self.day} {self.name}>"


class GoogleCalendarAccount(PrdStandardMixin, db.Model):
    """Stored OAuth token + target calendar for the optional Google sync.

    A single active row is used (the company's shared / personal calendar). The
    ``token`` JSON holds the google-auth Credentials info (refresh token, etc.).
    """

    __tablename__ = "google_calendar_accounts"

    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_id: Mapped[str] = mapped_column(String(255), nullable=False, default="primary")
    token: Mapped[dict | None] = mapped_column(JsonB, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<GoogleCalendarAccount {self.account_email or '—'} {self.calendar_id}>"
