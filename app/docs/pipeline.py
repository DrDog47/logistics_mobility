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

from app.docs.constants import DRIVER_DOCUMENT_VALUES, VEHICLE_DOCUMENT_VALUES
from app.docs.recognizer import RecognitionResult, RecognizerError, get_recognizer
from app.docs.services import inbox_dir

# Extension -> MIME type for the recognizer (only the accepted upload types).
_MIME_BY_EXT: dict[str, str] = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}

# Document types listed in the PRD catalogue (driver + vehicle). A recognised file
# whose type isn't one of these is "unrecognised format" — kept in the inbox but
# flagged and skipped by Recognise all (§8.2).
KNOWN_FORMAT_TYPES: frozenset[str] = DRIVER_DOCUMENT_VALUES | VEHICLE_DOCUMENT_VALUES

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


def is_recognizable_file(path: Path) -> bool:
    """True when the file's extension is one the recognizer can read (PDF/JPG/PNG).
    Other files are kept in the inbox but never fed to the recognizer (§8.2)."""
    return path.suffix.lstrip(".").lower() in _MIME_BY_EXT


def list_all_inbox_files() -> list[Path]:
    """Every top-level file in the inbox, recognisable or not (dot files and
    subfolders excluded).

    Unlike :func:`list_inbox_files`, this also surfaces unsupported file types so
    the operator can see them flagged in the inbox and remove or re-upload them
    (§8.2) — recognition itself still runs only over :func:`list_inbox_files`.
    """
    inbox = inbox_dir()
    return [
        entry
        for entry in sorted(inbox.iterdir())
        if entry.is_file() and not entry.name.startswith(".")
    ]


def recognize_file(path: Path) -> RecognizedFile:
    """Run the configured recognizer on a single file."""
    ext = path.suffix.lstrip(".").lower()
    size = path.stat().st_size
    if ext not in _MIME_BY_EXT:
        # Unsupported file type — keep it in the inbox but never feed it to the
        # recognizer; the UI surfaces it as an unrecognised-format file (§8.2).
        return RecognizedFile(
            path.name,
            size,
            "application/octet-stream",
            RecognitionResult(
                recognized=False,
                provider="-",
                note="unsupported file format",
            ),
        )
    mime = _MIME_BY_EXT[ext]
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


async def _recognize_file_async(recognizer, path: Path) -> RecognizedFile:
    """Async recognition of a single file using the given recognizer. Mirrors
    :func:`recognize_file` but awaits the recognizer so a batch can overlap."""
    ext = path.suffix.lstrip(".").lower()
    size = path.stat().st_size
    if ext not in _MIME_BY_EXT:
        return RecognizedFile(
            path.name,
            size,
            "application/octet-stream",
            RecognitionResult(recognized=False, provider="-", note="unsupported file format"),
        )
    mime = _MIME_BY_EXT[ext]
    try:
        content = path.read_bytes()
        result = await recognizer.arecognize(content=content, mime_type=mime, filename=path.name)
        return RecognizedFile(path.name, size, mime, result)
    except RecognizerError as exc:
        return RecognizedFile(path.name, size, mime, None, error=str(exc))


async def recognize_paths_async(
    recognizer, paths: list[Path], concurrency: int = 4
) -> list[RecognizedFile]:
    """Recognise ``paths`` concurrently via ``asyncio.gather`` (no threads/processes),
    capped by ``concurrency`` so a large inbox / the LLM rate limit isn't hammered.
    ``recognizer`` and ``paths`` are passed in so this runs without app context."""
    import asyncio

    sem = asyncio.Semaphore(concurrency)

    async def _one(p: Path) -> RecognizedFile:
        async with sem:
            return await _recognize_file_async(recognizer, p)

    return list(await asyncio.gather(*[_one(p) for p in paths]))


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


def is_known_format(item: RecognizedFile) -> bool:
    """True when recognition produced a document type listed in the PRD catalogue.

    Files that aren't recognised, or whose type isn't in :data:`KNOWN_FORMAT_TYPES`,
    are "unrecognised format": kept in the inbox and flagged in the UI, but not
    turned into a confirmation entry and skipped by Recognise all (§8.2)."""
    return bool(
        item.result
        and item.result.recognized
        and item.result.document_type in KNOWN_FORMAT_TYPES
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
