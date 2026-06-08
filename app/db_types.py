"""Portable column types and shared mixins for the PRD schema.

These definitions are the single source of truth for the PRD's standard fields
(UUID PK, soft-delete, timestamps) and the portable UUID/JSONB types. They work
on BOTH PostgreSQL (production, migrations) and SQLite (the test config):

- ``Uuid(as_uuid=True)`` renders as native ``UUID`` on Postgres and ``CHAR(32)``
  on SQLite; the Python side is always ``uuid.UUID``.
- ``JsonB`` is ``JSONB`` on Postgres and generic ``JSON`` (TEXT) on SQLite.
- The ``uuid`` PK uses a Python-side ``default=uuid.uuid4`` so inserts never rely
  on the database. The PRD's ``gen_random_uuid()`` server default is added in the
  migration DDL only (it would break SQLite ``create_all``).

This module must NOT import ``app.extensions.db`` — models import ``db`` from
there and these mixins from here, and mixing the two would create a circular
import.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, DateTime, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

# Native UUID on Postgres, CHAR(32) on SQLite. Python attribute is uuid.UUID.
UuidType = Uuid(as_uuid=True)

# JSONB on Postgres, JSON (TEXT) on SQLite.
JsonB = JSON().with_variant(JSONB(), "postgresql")


def uuid_or_none(value: object) -> uuid.UUID | None:
    """WTForms ``coerce`` for UUID SelectFields.

    HTML round-trips choice values as strings, so SelectField values come back
    as ``str``. Empty/sentinel values become ``None``; anything else is parsed
    into a ``uuid.UUID``.
    """
    if value in (None, "", "None"):
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class PrdStandardMixin:
    """The four standard PRD fields (§2.1) plus a soft-delete helper.

    ``uuid`` PK, ``created_at``, ``deleted_at``, ``is_deleted``. Soft-deleted
    rows are kept in the table; every query must filter ``is_deleted.is_(False)``.
    """

    uuid: Mapped[uuid.UUID] = mapped_column(
        UuidType,
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa.false(),
    )

    def soft_delete(self) -> None:
        """Mark the row as deleted without removing it from the table."""
        self.is_deleted = True
        self.deleted_at = datetime.now(UTC)


class UpdatedAtMixin:
    """Adds ``updated_at`` (PRD §3.1/§7.1 — only driver & organisation).

    Maintained at the ORM layer via ``onupdate`` rather than the PRD's DB
    trigger, which keeps SQLite parity for tests.
    """

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )