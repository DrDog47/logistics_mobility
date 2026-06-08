"""Trip and TripSegment models.

A Trip is a logical journey (e.g. PL -> DE -> FR -> PL) over several days.
A TripSegment is a single contiguous chunk of work in ONE country with ONE
Mobility Package classification. Manually entered in Phase 1; auto-generated
from tachograph + GPS in Phase 4.

Key invariant: TripSegment.segment_type drives whether the foreign sector
wage applies. Only CABOTAGE and CROSS_TRADE count as "delegowanie".
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db_types import UuidType
from app.extensions import db


class SegmentType(str, enum.Enum):
    """Mobility Package trip classification.

    Only CABOTAGE and CROSS_TRADE require foreign sector-wage equalization
    and IMI registration. TRANSIT and BILATERAL are exempt.
    """

    TRANSIT = "transit"          # Passing through, no load/unload
    BILATERAL = "bilateral"      # PL <-> X (with up to 2 add'l stops). No posting.
    CABOTAGE = "cabotage"        # X -> X (transport within one foreign country). Posting.
    CROSS_TRADE = "cross_trade"  # X -> Y (between two foreign countries). Posting.

    @property
    def is_posting(self) -> bool:
        """Whether this segment triggers the Posted Workers Directive rules."""
        return self in (SegmentType.CABOTAGE, SegmentType.CROSS_TRADE)


class TripStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"      # Locked from edits; included in payroll runs


class Trip(db.Model):
    """A logical journey — header for one or more segments."""

    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(primary_key=True)

    driver_id: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("drivers.uuid", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType,
        ForeignKey("vehicles.uuid", ondelete="SET NULL"),
        nullable=True,
    )

    trip_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[TripStatus] = mapped_column(
        Enum(TripStatus),
        nullable=False,
        default=TripStatus.DRAFT,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    driver = relationship("Driver", lazy="joined")
    vehicle = relationship("Vehicle", lazy="joined")
    segments: Mapped[list[TripSegment]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
        order_by="TripSegment.sequence",
    )

    @property
    def total_hours(self) -> Decimal:
        return sum((s.work_hours for s in self.segments), Decimal("0"))

    @property
    def posting_hours(self) -> Decimal:
        return sum(
            (s.work_hours for s in self.segments if s.segment_type.is_posting),
            Decimal("0"),
        )

    def __repr__(self) -> str:
        return f"<Trip {self.trip_number} {self.start_date}..{self.end_date}>"


class TripSegment(db.Model):
    """A single country/classification chunk within a trip."""

    __tablename__ = "trip_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    segment_type: Mapped[SegmentType] = mapped_column(Enum(SegmentType), nullable=False)
    work_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    rate_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="driver_default",
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    trip: Mapped[Trip] = relationship(back_populates="segments")

    def __repr__(self) -> str:
        return (
            f"<TripSegment trip={self.trip_id} {self.work_date} "
            f"{self.country}/{self.segment_type.value} {self.work_hours}h>"
        )
