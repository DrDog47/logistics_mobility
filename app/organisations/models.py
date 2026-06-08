"""Organisation model (PRD §7) — employer companies for drivers and vehicles."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db_types import PrdStandardMixin, UpdatedAtMixin
from app.extensions import db


class Organisation(PrdStandardMixin, UpdatedAtMixin, db.Model):
    """A company that employs drivers and owns vehicles."""

    __tablename__ = "organisation"

    # national_id: NIP (PL), ИНН (RU), EDRPOU (UA), ... — format varies by country.
    national_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(3), nullable=False, index=True)  # ISO 3166-1 alpha-3
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)

    drivers = relationship("Driver", back_populates="organisation")
    vehicles = relationship("Vehicle", back_populates="organisation")

    def __repr__(self) -> str:
        return f"<Organisation {self.name} ({self.country})>"