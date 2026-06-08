"""Helpers shared by document routes and forms."""

from __future__ import annotations

from app.documents.constants import BASE_DOCUMENT_TYPES
from app.documents.models import DocumentType
from app.extensions import db


def document_type_choices(entity_type: str) -> list[tuple[str, str]]:
    """``(value, label)`` choices for a document-type SelectField.

    Reads the active rows of the ``document_type`` catalogue for the given
    entity. Falls back to the base constant list when the table has no rows yet
    (fresh installs / tests run on an empty ``create_all`` schema).
    """
    rows = db.session.execute(
        db.select(DocumentType)
        .where(
            DocumentType.entity_type == entity_type,
            DocumentType.is_deleted.is_(False),
        )
        .order_by(DocumentType.type)
    ).scalars().all()
    if rows:
        return [(r.type, r.display_label) for r in rows]
    return list(BASE_DOCUMENT_TYPES.get(entity_type, []))


def parse_file_links(raw: str | None) -> list[str] | None:
    """Split a textarea (one URL per line) into a clean list, or ``None``."""
    if not raw:
        return None
    links = [line.strip() for line in raw.splitlines() if line.strip()]
    return links or None