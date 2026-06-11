"""Inbox recognition pipeline (PRD §8.2, §8.5–8.7).

Phase two of the two-phase flow: files already landed in ``_Inbox/`` (§8.6); here
we read each one and run it through the configured ``DocumentRecognizer`` to get
a structured preview. This module does NOT persist anything or move files — it
produces the data for the confirmation screen. Writing to the DB and sorting
files into driver folders is the next step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.docs.recognizer import RecognitionResult, RecognizerError, get_recognizer
from app.docs.services import inbox_dir

# Extension -> MIME type for the recognizer (only the accepted upload types).
_MIME_BY_EXT: dict[str, str] = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}

# Passport-family types that create/update drivers (§8.4). Shared with the
# persistence layer so "what is a passport" lives in one place.
PASSPORT_TYPES: frozenset[str] = frozenset({"passport", "passport_eu"})

# Entity-creating "trigger" documents that must be processed before any other
# file in the inbox (§8.4): a driver passport creates the driver; a vehicle
# **technical passport** (registration certificate, ``tech_passport``) creates
# the vehicle. Everything else attaches to an entity that must already exist, so
# these go first and the rest stay locked until they are processed. Kept
# separate from PASSPORT_TYPES, which drives the *driver-only* upsert.
TRIGGER_TYPES: frozenset[str] = PASSPORT_TYPES | frozenset({"tech_passport"})


@dataclass(frozen=True, slots=True)
class RecognizedFile:
    """One inbox file and its recognition outcome (or error)."""

    filename: str
    size: int
    mime_type: str
    result: RecognitionResult | None
    error: str | None = None


def list_inbox_files() -> list[Path]:
    """Return recognisable files sitting directly in the inbox.

    Only top-level files with an accepted extension; subfolders (e.g. anything
    the operator nests) and hidden/dot files are skipped.
    """
    inbox = inbox_dir()
    files: list[Path] = []
    for entry in sorted(inbox.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        ext = entry.suffix.lstrip(".").lower()
        if ext in _MIME_BY_EXT:
            files.append(entry)
    return files


def recognize_file(path: Path) -> RecognizedFile:
    """Run the configured recognizer on a single file."""
    ext = path.suffix.lstrip(".").lower()
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    size = path.stat().st_size
    try:
        content = path.read_bytes()
        result = get_recognizer().recognize(
            content=content, mime_type=mime, filename=path.name
        )
        return RecognizedFile(path.name, size, mime, result)
    except RecognizerError as exc:
        return RecognizedFile(path.name, size, mime, None, error=str(exc))


def recognize_inbox() -> list[RecognizedFile]:
    """Recognise every file currently in the inbox (synchronous)."""
    return [recognize_file(p) for p in list_inbox_files()]


def sort_passports_first(recognized: list[RecognizedFile]) -> list[RecognizedFile]:
    """Stable-sort passports to the top so the operator processes them first
    (drivers must exist before their other documents attach — §8.4). Order of
    everything else is preserved."""
    return sorted(
        recognized,
        key=lambda r: 0 if (r.result and r.result.document_type in PASSPORT_TYPES) else 1,
    )


def passport_count(recognized: list[RecognizedFile]) -> int:
    """How many recognised entries are passports (drives the inbox warning)."""
    return sum(
        1 for r in recognized if r.result and r.result.document_type in PASSPORT_TYPES
    )


def is_trigger(item: RecognizedFile) -> bool:
    """True when a recognised file is an entity-creating trigger document
    (passport or technical passport) — see :data:`TRIGGER_TYPES`."""
    return bool(item.result and item.result.document_type in TRIGGER_TYPES)


def sort_triggers_first(recognized: list[RecognizedFile]) -> list[RecognizedFile]:
    """Stable-sort trigger documents (passports + technical passports) to the top
    so the operator processes them first — the entity they create must exist
    before any of its other documents can attach (§8.4)."""
    return sorted(recognized, key=lambda r: 0 if is_trigger(r) else 1)


def pending_trigger_count(recognized: list[RecognizedFile]) -> int:
    """How many recognised entries are still-unprocessed trigger documents
    (drives the 'process these first / others locked' gate)."""
    return sum(1 for r in recognized if is_trigger(r))


def inbox_has_pending_trigger(exclude: set[str] | None = None) -> bool:
    """True if any inbox file (other than ``exclude``) classifies as a trigger
    document. Server-side guard so a non-trigger file can't be recognised /
    processed while a passport or technical passport is still waiting. Recognises
    files lazily and short-circuits on the first trigger found."""
    exclude = exclude or set()
    for path in list_inbox_files():
        if path.name in exclude:
            continue
        if is_trigger(recognize_file(path)):
            return True
    return False
