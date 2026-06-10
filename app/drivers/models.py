"""Driver model.

Driver is aligned to the PRD (UUID PK, soft-delete, organisation link, ``extra``
JSONB) while keeping the legacy ``first_name``/``last_name`` columns and the
identification fields used by the rest of the app.

Contracts are no longer a separate table — a contract is a ``DriverDocument`` of
the ``employment`` type (see :mod:`app.drivers.contracts`).
"""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db_types import JsonB, PrdStandardMixin, UpdatedAtMixin, UuidType
from app.extensions import db


class ContractType(str, enum.Enum):
    """Types of driver contracts under Polish law.

    Stored as the ``contract_type`` key in an employment document's ``extra``.
    """

    UMOWA_O_PRACE = "umowa_o_prace"      # Employment contract — full ZUS + PIT
    UMOWA_ZLECENIA = "umowa_zlecenia"    # Mandate contract — partial ZUS scenarios
    B2B = "b2b"                          # Self-employed — invoice-based, no PM wage rules


class Driver(PrdStandardMixin, UpdatedAtMixin, db.Model):
    """Driver employed by the company. Independent of any single contract."""

    __tablename__ = "drivers"

    # Identification (first_name/last_name hold the passport Latin spelling).
    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    last_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # birth_date / nationality are nullable so a driver auto-created from a
    # passport with partial data can exist until it's completed manually (§8.4).
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(3), nullable=True)  # ISO 3166-1 alpha-3
    # identification_id = passport number; the PRD's unique business key.
    identification_id: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    pesel: Mapped[str | None] = mapped_column(String(11), unique=True, nullable=True)
    passport_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tachograph_card_number: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)

    # Contact / notes (the "few more inputs").
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Employment. hire_date is nullable so a driver auto-created from a passport
    # (which carries no hire date) can exist until it's filled in manually (§8.4).
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Organisation link (PRD §3). Nullable: a driver auto-created from a document
    # package has no organisation until an operator assigns it manually (§8.4).
    organisation_uuid: Mapped[uuid.UUID | None] = mapped_column(
        UuidType,
        ForeignKey("organisation.uuid", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # Extensible attributes (PRD §3.4).
    extra: Mapped[dict | None] = mapped_column(JsonB, nullable=True)

    # Relationships
    organisation = relationship("Organisation", back_populates="drivers")
    documents = relationship(
        "DriverDocument",
        back_populates="driver",
        order_by="DriverDocument.end_date",
    )

    @property
    def active_documents(self) -> list:
        """Current documents — excludes soft-deleted and archived (§8.6)."""
        return [
            d for d in self.documents
            if not d.is_deleted and d.archived_at is None
        ]

    @property
    def non_contract_documents(self) -> list:
        """Active documents shown in the Documents table — excludes contracts,
        which are managed in their own section (see :mod:`app.drivers.contracts`)."""
        from app.drivers.contracts import EMPLOYMENT_DOC_TYPE

        return [d for d in self.active_documents if d.document_type != EMPLOYMENT_DOC_TYPE]

    @property
    def contract_documents(self) -> list:
        """Active employment documents (contracts), newest first."""
        from app.drivers.contracts import contract_documents

        return contract_documents(self)

    @property
    def archived_documents(self) -> list:
        """Outdated versions moved to Archive/ — kept as history (§8.6)."""
        return [
            d for d in self.documents
            if not d.is_deleted and d.archived_at is not None
        ]

    @property
    def id(self) -> uuid.UUID:
        """Legacy alias for the primary key (templates/url_for use ``id``)."""
        return self.uuid

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def current_contract(self):
        """The employment document active today (a :class:`DriverDocument`), if any."""
        from app.drivers.contracts import current_contract_doc

        return current_contract_doc(self)

    def __repr__(self) -> str:
        return f"<Driver {self.full_name}>"