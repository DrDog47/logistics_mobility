"""Document models (PRD §4, §6) plus the ``document_type`` catalogue.

Driver and vehicle documents share a single ``document`` table via SQLAlchemy
single-table inheritance: ``Document`` is the base, ``entity_type`` is the
polymorphic discriminator, and ``DriverDocument`` / ``VehicleDocument`` are the
concrete subclasses (each owning only the columns specific to it). ``DocumentType``
is the operator-editable catalogue of allowed types, keyed by ``(type,
entity_type)``; every document carries a composite FK into it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db_types import JsonB, PrdStandardMixin, UuidType
from app.docs.constants import ENTITY_DRIVER, ENTITY_VEHICLE
from app.extensions import db


class DocumentType(PrdStandardMixin, db.Model):
    """Catalogue of allowed document types (PRD §4.4 / §6.4).

    Replaces the hard-coded ``document_type`` enum-like lists: operators add new
    types from the UI. ``(type, entity_type)`` is unique and is the target of the
    composite FK on the ``document`` table.
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


class Document(PrdStandardMixin, db.Model):
    """Base of the single-table document hierarchy (table ``document``).

    Holds the columns common to every document. ``entity_type`` is the
    polymorphic discriminator — SQLAlchemy sets it from each subclass's
    ``polymorphic_identity`` on insert and scopes subclass queries to it. The
    entity-specific columns (``driver_uuid`` / ``vehicle_uuid`` / ``file_links``)
    live on the subclasses and are nullable on the shared table.
    """

    __tablename__ = "document"

    # Polymorphic discriminator + half of the composite FK into document_type.
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Set when an outdated version is moved to Archive/ (PRD §8.6). Archived rows
    # are kept (history) but are not the current/controlling document.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Type-specific attributes (e.g. license categories, insurance company).
    extra: Mapped[dict | None] = mapped_column(JsonB, nullable=True)

    __mapper_args__ = {"polymorphic_on": entity_type}

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_type", "entity_type"],
            ["document_type.type", "document_type.entity_type"],
            ondelete="RESTRICT",
            name="fk_document_type",
        ),
    )


class DriverDocument(Document):
    __mapper_args__ = {"polymorphic_identity": ENTITY_DRIVER}

    # Nullable on the shared table (vehicle rows leave it NULL); always set for
    # driver documents at the application layer.
    driver_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UuidType,
        ForeignKey("drivers.uuid", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    driver = relationship("Driver", back_populates="documents")
    # A document owns its physical files (front/back, pages) — one-to-many.
    files = relationship(
        "DriverFile",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DriverFile.created_at",
    )

    @property
    def scan_links(self) -> list[str]:
        """Storage links for this document's scans (parallels VehicleDocument)."""
        return [f.file_link for f in self.files if not f.is_deleted]

    def sync_tachograph_number_to_driver(self, driver=None) -> bool:
        """Rule: a confirmed tachograph-card document is the source of truth for
        the driver's tachograph card number — copy its number onto the driver so
        the two always match. No-op for other document types or an invalid/empty
        number. Returns ``True`` when the driver's number was changed.

        ``driver`` may be passed explicitly (e.g. before the row is flushed);
        otherwise the ``driver`` relationship is used.
        """
        from app.docs.constants import TACHOGRAPH_DOC_TYPE
        from app.docs.validation import is_tacho

        driver = driver if driver is not None else self.driver
        if (
            self.document_type == TACHOGRAPH_DOC_TYPE
            and self.document_id
            and is_tacho(self.document_id)
            and driver is not None
            and driver.tachograph_card_number != self.document_id
        ):
            driver.tachograph_card_number = self.document_id
            return True
        return False

    def __repr__(self) -> str:
        return f"<DriverDocument {self.document_type} driver={self.driver_uuid}>"


class VehicleDocument(Document):
    __mapper_args__ = {"polymorphic_identity": ENTITY_VEHICLE}

    vehicle_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UuidType,
        ForeignKey("vehicles.uuid", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    vehicle = relationship("Vehicle", back_populates="documents")
    # Vehicle scans stay as a JSONB array of links (driver files were normalised
    # into the driver_file table; vehicles keep the simpler shape for now).
    file_links: Mapped[list | None] = mapped_column(JsonB, nullable=True)

    @property
    def scan_links(self) -> list[str]:
        return list(self.file_links or [])

    def __repr__(self) -> str:
        return f"<VehicleDocument {self.document_type} vehicle={self.vehicle_uuid}>"


class DriverFile(PrdStandardMixin, db.Model):
    """One physical file belonging to a :class:`DriverDocument` (PRD §8 / TDD §8a).

    A document may have several files (front/back, multi-page). Each file keeps the
    metadata recognised *from it* (type/number/dates/extra) alongside its storage
    ``file_link``; the parent document holds the canonical values. ``document_uuid``
    is NOT NULL — a file that cannot be tied to a document stays in ``_Inbox/`` for
    manual processing rather than being recorded here.
    """

    __tablename__ = "driver_file"

    # Recognised-from-this-file metadata (all optional — present only if read).
    document_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Where the file is stored (relative path in the document tree, or a URL).
    file_link: Mapped[str] = mapped_column(Text, nullable=False)
    # Loose per-file attributes that don't warrant their own column.
    extra: Mapped[dict | None] = mapped_column(JsonB, nullable=True)

    document_uuid: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        ForeignKey("document.uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document = relationship("DriverDocument", back_populates="files")

    def __repr__(self) -> str:
        return f"<DriverFile {self.file_link} document={self.document_uuid}>"
