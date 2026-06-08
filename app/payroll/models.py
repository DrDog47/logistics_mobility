"""Payroll persistence: periods, line items, results.

Phase 2 adds:
- Days-abroad tracking (auto from segments + manual override)
- ZUS base, PIT base, and net pay denormalized on the period
- Line types for virtual diets, sanitariaty, employee deductions
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class PayrollStatus(str, enum.Enum):
    DRAFT = "draft"
    CALCULATED = "calculated"
    APPROVED = "approved"
    PAID = "paid"


class PayrollLineType(str, enum.Enum):
    """Categorization of line items.

    Three kinds of effect on totals:
    1. Adds to gross: BASE_SALARY, FOREIGN_WAGE, EQUALIZATION
    2. Reduces taxation bases without affecting gross: VIRTUAL_DIET_ZUS,
       VIRTUAL_DIET_PIT (informational; their amount_pln is the deduction)
    3. Reduces gross to get net: ZUS_EMPLOYEE, ZDROWOTNE, PIT_ADVANCE
    4. Added to net after tax: SANITARIATY (reimbursement, ZUS/PIT exempt)
    """

    BASE_SALARY = "base_salary"
    FOREIGN_WAGE = "foreign_wage"
    EQUALIZATION = "equalization"

    VIRTUAL_DIET_ZUS = "virtual_diet_zus"
    VIRTUAL_DIET_PIT = "virtual_diet_pit"

    SANITARIATY = "sanitariaty"

    ZUS_EMPLOYEE = "zus_employee"     # 13.71% of zus_base
    ZDROWOTNE = "zdrowotne"           # 9% of (gross - zus_employee)
    PIT_ADVANCE = "pit_advance"       # simplified 12% of pit_base


class PayrollPeriod(db.Model):
    __tablename__ = "payroll_periods"
    __table_args__ = (
        UniqueConstraint("driver_id", "year", "month", name="uq_period_driver_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(
        ForeignKey("drivers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PayrollStatus] = mapped_column(
        Enum(PayrollStatus),
        nullable=False,
        default=PayrollStatus.DRAFT,
    )

    # Phase 2: EUR/PLN comes from NBP cache (linked) but value is denormalized
    # for fast reads. Override allowed for edge cases.
    eur_pln_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    nbp_rate_id: Mapped[int | None] = mapped_column(
        ForeignKey("nbp_rates.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Days abroad: auto-computed from segments; user can override
    days_abroad_auto: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    days_abroad_override: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Result totals (denormalized)
    total_gross_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    foreign_wage_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    equalization_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    zus_base_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    pit_base_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    sanitariaty_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    zus_employee_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    zdrowotne_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    pit_advance_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_net_pln: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calculator_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    driver = relationship("Driver", lazy="joined")
    nbp_rate = relationship("NbpRate", lazy="joined")
    lines: Mapped[list["PayrollLine"]] = relationship(
        back_populates="period",
        cascade="all, delete-orphan",
        order_by="PayrollLine.id",
    )

    @property
    def period_label(self) -> str:
        return f"{self.year}-{self.month:02d}"

    @property
    def effective_days_abroad(self) -> int:
        return self.days_abroad_override if self.days_abroad_override is not None else self.days_abroad_auto

    def __repr__(self) -> str:
        return f"<PayrollPeriod driver={self.driver_id} {self.period_label} {self.status.value}>"


class PayrollLine(db.Model):
    __tablename__ = "payroll_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_periods.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    line_type: Mapped[PayrollLineType] = mapped_column(Enum(PayrollLineType), nullable=False)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    rate_hourly_native: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    rate_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    amount_native: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_pln: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("country_rate_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )

    description: Mapped[str] = mapped_column(String(300), nullable=False)

    period: Mapped[PayrollPeriod] = relationship(back_populates="lines")
    snapshot = relationship("CountryRateSnapshot", lazy="joined")
