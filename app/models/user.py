"""User account model with role-based access control."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

import bcrypt
from flask_login import UserMixin
from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db, login_manager


class Role(str, enum.Enum):
    """User roles. Order = privilege level (admin > accountant > fleet_manager)."""

    ADMIN = "admin"
    ACCOUNTANT = "accountant"
    FLEET_MANAGER = "fleet_manager"


class User(UserMixin, db.Model):
    """Application user (employee with access to the payroll system)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False, default=Role.FLEET_MANAGER)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --- Password handling ---------------------------------------------------

    def set_password(self, password: str) -> None:
        """Hash and store a password using bcrypt."""
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        salt = bcrypt.gensalt(rounds=12)
        self.password_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    def check_password(self, password: str) -> bool:
        """Verify a password against the stored hash."""
        if not self.password_hash:
            return False
        return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))

    # --- Role helpers --------------------------------------------------------

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_accountant(self) -> bool:
        return self.role == Role.ACCOUNTANT

    @property
    def is_fleet_manager(self) -> bool:
        return self.role == Role.FLEET_MANAGER

    def __repr__(self) -> str:
        return f"<User {self.login} ({self.role.value})>"


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Flask-Login callback."""
    return db.session.get(User, int(user_id))
