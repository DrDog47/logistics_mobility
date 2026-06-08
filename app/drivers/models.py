"""Driver and DriverContract models."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class ContractType(str, enum.Enum):
    """Types of driver contracts under Polish law."""

    UMOWA_O_PRACE = "umowa_o_prace"      # Employment contract — full ZUS + PIT
    UMOWA_ZLECENIA = "umowa_zlecenia"    # Mandate contract — partial ZUS scenarios
    B2B = "b2b"                          # Self-employed — invoice-based, no PM wage rules


class Driver(db.Model):
    """Driver employed by the company. Independent of any single contract."""

    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identification
    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    last_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    nationality: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 3166-1 alpha-3
    pesel: Mapped[str | None] = mapped_column(String(11), unique=True, nullable=True)
    passport_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tachograph_card_number: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)

    # Employment
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    contracts: Mapped[list[DriverContract]] = relationship(
        back_populates="driver",
        cascade="all, delete-orphan",
        order_by="DriverContract.start_date.desc()",
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def current_contract(self) -> DriverContract | None:
        """Returns the contract active today, if any."""
        today = date.today()
        for contract in self.contracts:
            if contract.start_date <= today and (
                contract.end_date is None or contract.end_date >= today
            ):
                return contract
        return None

    def __repr__(self) -> str:
        return f"<Driver {self.full_name}>"


class DriverContract(db.Model):
    """A single contract period for a driver. Multiple contracts can exist over time."""

    __tablename__ = "driver_contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    contract_type: Mapped[ContractType] = mapped_column(Enum(ContractType), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Base salary in PLN (gross for pracę/zlecenia, agreed rate for B2B)
    base_salary_pln: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    # Monthly working hours norm (for KP-based contracts; ignored for B2B)
    hours_norm: Mapped[int] = mapped_column(default=168, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    driver: Mapped[Driver] = relationship(back_populates="contracts")

    def __repr__(self) -> str:
        return f"<DriverContract {self.contract_type.value} {self.start_date}..{self.end_date or 'open'}>"
