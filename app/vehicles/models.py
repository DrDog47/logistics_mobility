"""Vehicle model: tractors and trailers (PRD §5)."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db_types import JsonB, PrdStandardMixin, UuidType
from app.extensions import db


class VehicleType:
    """Allowed ``vehicle_type`` values (plain strings, not a DB enum — PRD §5.4)."""

    TRACTOR = "tractor"   # Tractor unit / truck
    TRAILER = "trailer"   # Semi-trailer


VEHICLE_TYPE_CHOICES: list[tuple[str, str]] = [
    (VehicleType.TRACTOR, "Tractor"),
    (VehicleType.TRAILER, "Trailer"),
]


class Vehicle(PrdStandardMixin, db.Model):
    __tablename__ = "vehicles"

    vehicle_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    vin: Mapped[str] = mapped_column(String(17), unique=True, nullable=False)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    registration_plate: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    manufacture_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    organisation_uuid: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("organisation.uuid", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    extra: Mapped[dict | None] = mapped_column(JsonB, nullable=True)

    organisation = relationship("Organisation", back_populates="vehicles")
    documents = relationship(
        "VehicleDocument",
        back_populates="vehicle",
        order_by="VehicleDocument.end_date",
    )

    @property
    def id(self) -> uuid.UUID:
        """Legacy alias for the primary key (templates/url_for use ``id``)."""
        return self.uuid

    def __repr__(self) -> str:
        return f"<Vehicle {self.registration_plate} ({self.vehicle_type})>"