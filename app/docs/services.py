"""Helpers shared by document routes and forms."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.security import safe_join

from app.docs.constants import BASE_DOCUMENT_TYPES
from app.docs.models import DocumentType, DriverDocument, DriverFile
from app.extensions import db

# Control characters plus path separators and Windows-reserved characters. We
# strip these (rather than the whole non-ASCII range) so Cyrillic / Polish names
# survive — the inbox filename feeds the recogniser's name parsing.
_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


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


def active_driver_documents(driver_uuid=None) -> list[DriverDocument]:
    """Active (non-deleted, non-archived) driver documents, optionally limited to
    one driver. Used to let an operator attach/reattach a file to an existing
    document (§8.5)."""
    query = (
        db.select(DriverDocument)
        .where(
            DriverDocument.is_deleted.is_(False),
            DriverDocument.archived_at.is_(None),
        )
        .order_by(DriverDocument.document_type)
    )
    if driver_uuid is not None:
        query = query.where(DriverDocument.driver_uuid == driver_uuid)
    return db.session.execute(query).scalars().all()


def document_label(doc: DriverDocument) -> str:
    """Short human label for a document picker: ``type · number · → end_date``."""
    parts: list[str] = [doc.document_type]
    if doc.document_id:
        parts.append(doc.document_id)
    if doc.end_date:
        parts.append(f"→ {doc.end_date.isoformat()}")
    return " · ".join(parts)


def document_type_label(entity_type: str, code: str | None) -> str:
    """Human label for a document-type code (catalogue ``display_label``), with a
    prettified fallback. Used by the documents table across entities."""
    if not code:
        return ""
    for value, label in document_type_choices(entity_type):
        if value == code:
            return label
    return code.replace("_", " ").capitalize()


def stored_file_size(file_link: str | None) -> str | None:
    """Human-readable size of a locally stored file, or ``None`` (external URL /
    missing). Shown in the file cards of the documents table."""
    path = resolve_stored_file(file_link)
    if path is None:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def parse_file_links(raw: str | None) -> list[str] | None:
    """Split a textarea (one URL per line) into a clean list, or ``None``."""
    if not raw:
        return None
    links = [line.strip() for line in raw.splitlines() if line.strip()]
    return links or None


def documents_root() -> Path:
    """Absolute root of the document tree (``DOCUMENTS_DIR``), where file_link
    paths are resolved for serving/downloading."""
    return Path(current_app.config["DOCUMENTS_DIR"]).resolve()


def resolve_stored_file(file_link: str | None) -> Path | None:
    """Resolve a stored ``file_link`` (relative path) to an absolute path under
    the document root, or ``None`` for external URLs, traversal attempts, or a
    missing file. ``safe_join`` rejects ``..`` and absolute escapes."""
    if not file_link:
        return None
    if "://" in file_link:  # external URL — not a local file we serve
        return None
    root = documents_root()
    joined = safe_join(str(root), file_link)
    if joined is None:
        return None
    path = Path(joined)
    return path if path.is_file() else None


def set_driver_document_files(doc: DriverDocument, raw: str | None) -> None:
    """Sync a driver document's files from a 'one link per line' textarea.

    Replaces the document's ``driver_file`` rows (delete-orphan via the
    relationship) with one row per non-empty line — file_link only, no per-file
    metadata, since the operator is editing links by hand here.
    """
    doc.files.clear()
    for link in parse_file_links(raw) or []:
        doc.files.append(DriverFile(file_link=link))


# --- Bulk upload to the inbox (PRD §8.6) -------------------------------------


def inbox_dir() -> Path:
    """Path to the inbox folder inside the configured document tree.

    Created on demand. In production ``DOCUMENTS_DIR`` is an external folder
    mounted into the container; here we just ensure the inbox subfolder exists.
    """
    root: Path = Path(current_app.config["DOCUMENTS_DIR"])
    inbox = root / current_app.config["DOCUMENTS_INBOX_DIRNAME"]
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def safe_inbox_filename(original: str | None) -> str | None:
    """Sanitise an uploaded filename for storage in the inbox.

    Unlike ``werkzeug.secure_filename`` this preserves Unicode letters (Cyrillic,
    Polish diacritics, …) so a driver's name stays intact in the stored filename
    — which matters because the recogniser parses ``<First>_<Last>_<Type>…`` from
    it. It still neutralises the dangerous parts:

    * drops any directory component (both ``/`` and ``\\``) — no path traversal;
    * removes control characters and path/Windows-reserved characters;
    * trims leading/trailing dots and whitespace (no hidden/anchored names);
    * collapses internal whitespace to ``_`` (the TZ naming convention).

    Returns ``None`` when nothing usable remains (e.g. an empty or ``..`` name).
    """
    if not original:
        return None
    # Keep only the final path component, regardless of separator style.
    name = original.replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    name = _UNSAFE_FILENAME_CHARS.sub("", name)
    name = re.sub(r"\s+", "_", name.strip())
    name = name.strip("._")  # no leading/trailing dots (hidden) or stray underscores
    if name in ("", ".", ".."):
        return None
    return name


def _unique_path(directory: Path, filename: str) -> Path:
    """Return a non-colliding path in ``directory`` for ``filename``."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, dot, ext = filename.rpartition(".")
    base = stem if dot else filename
    suffix = f".{ext}" if dot else ""
    i = 1
    while True:
        candidate = directory / f"{base}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def save_uploads_to_inbox(
    files: list[FileStorage],
) -> tuple[list[str], list[dict[str, str]]]:
    """Save uploaded files into the inbox, returning ``(saved, rejected)``.

    Recognition / sorting into driver folders happens later (PRD §8.4–8.6);
    this only lands the raw package in ``_Inbox/``. ``saved`` is the list of
    stored filenames; ``rejected`` is ``[{"name", "reason"}]`` for skipped files.

    Files of any type are accepted (§8.2 — let the operator upload unrecognised
    formats too); unsupported types simply land in the inbox flagged for review
    rather than being rejected at upload.
    """
    destination = inbox_dir()
    saved: list[str] = []
    rejected: list[dict[str, str]] = []

    for storage in files:
        original = storage.filename or ""
        if not original:
            continue
        safe = safe_inbox_filename(original)
        if not safe:
            rejected.append({"name": original, "reason": "bad_name"})
            continue
        target = _unique_path(destination, safe)
        storage.save(target)
        saved.append(target.name)

    return saved, rejected