"""Document models (PRD §4, §6) plus the ``document_type`` catalogue.

``DriverDocument`` and ``VehicleDocument`` deliberately mirror each other; the
shared columns live on ``_DocumentMixin``. ``DocumentType`` is the operator-
editable catalogue of allowed types, keyed by ``(type, entity_type)``; each
document carries a composite FK into it.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db_types import JsonB, PrdStandardMixin, UuidType
from app.documents.constants import ENTITY_DRIVER, ENTITY_VEHICLE
from app.extensions import db


class DocumentType(PrdStandardMixin, db.Model):
    """Catalogue of allowed document types (PRD §4.4 / §6.4).

    Replaces the hard-coded ``document_type`` enum-like lists: operators add new
    types from the UI. ``(type, entity_type)`` is unique and is the target of the
    composite FK on ``driver_document`` / ``vehicle_document``.
    """

    __tablename__ = "document_type"

    # The document-type code, e.g. "passport", "insurance". Stored on documents.
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Who the type belongs to: driver / vehicle / organisation.
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Human-readable name shown in dropdowns; falls back to ``type`` when unset.
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint("type", "entity_type", name="uq_document_type_type_entity"),
    )

    @property
    def display_label(self) -> str:
        return self.label or self.type

    def __repr__(self) -> str:
        return f"<DocumentType {self.type}/{self.entity_type}>"


class _DocumentMixin(PrdStandardMixin):
    """Columns common to driver and vehicle documents."""

    document_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # A document may have several scans — list of URLs stored as JSONB (PRD §4 task).
    file_links: Mapped[list | None] = mapped_column(JsonB, nullable=True)
    # Type-specific attributes (e.g. license categories, insurance company).
    extra: Mapped[dict | None] = mapped_column(JsonB, nullable=True)


class DriverDocument(_DocumentMixin, db.Model):
    __tablename__ = "driver_document"

    # Constant discriminator that pairs with document_type in the composite FK.
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ENTITY_DRIVER
    )
    driver_uuid: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("drivers.uuid", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    driver = relationship("Driver", back_populates="documents")

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_type", "entity_type"],
            ["document_type.type", "document_type.entity_type"],
            ondelete="RESTRICT",
            name="fk_driver_document_type",
        ),
    )

    def __repr__(self) -> str:
        return f"<DriverDocument {self.document_type} driver={self.driver_uuid}>"


class VehicleDocument(_DocumentMixin, db.Model):
    __tablename__ = "vehicle_document"

    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ENTITY_VEHICLE
    )
    vehicle_uuid: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("vehicles.uuid", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    vehicle = relationship("Vehicle", back_populates="documents")

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_type", "entity_type"],
            ["document_type.type", "document_type.entity_type"],
            ondelete="RESTRICT",
            name="fk_vehicle_document_type",
        ),
    )

    def __repr__(self) -> str:
        return f"<VehicleDocument {self.document_type} vehicle={self.vehicle_uuid}>"