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


# --- Recognition result cache ----------------------------------------------
# Without this, the trigger gate (`inbox_has_pending_trigger`) and repeat
# recognise clicks would re-run a full identify+extract on the same inbox file
# every time. Cache each file's RecognizedFile by a cheap signature
# (name, size, mtime) so it's recognised once; a changed / re-uploaded file
# (different size or mtime) misses and is recognised afresh. Failures are NOT
# cached, so a transient recognizer error can be retried.
_recognition_cache: dict[tuple[str, int, int], RecognizedFile] = {}


def _file_signature(path: Path) -> tuple[str, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (path.name, st.st_size, st.st_mtime_ns)


def cached_recognition(path: Path) -> RecognizedFile | None:
    """The cached recognition for ``path`` if its signature still matches."""
    sig = _file_signature(path)
    return _recognition_cache.get(sig) if sig is not None else None


def _cache_recognition(path: Path, item: RecognizedFile) -> None:
    sig = _file_signature(path)
    if sig is not None:
        _recognition_cache[sig] = item


def _prune_recognition_cache(active_names: set[str]) -> None:
    """Drop cache entries for files no longer in the inbox (bounds growth)."""
    stale = [key for key in _recognition_cache if key[0] not in active_names]
    for key in stale:
        del _recognition_cache[key]


def clear_recognition_cache() -> None:
    """Forget all cached recognitions (e.g. when the inbox is cleared)."""
    _recognition_cache.clear()


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
    """Run the configured recognizer on a single file (cached by file signature)."""
    cached = cached_recognition(path)
    if cached is not None:
        return cached
    ext = path.suffix.lstrip(".").lower()
    size = path.stat().st_size
    if ext not in _MIME_BY_EXT:
        # Unsupported file type — keep it in the inbox but never feed it to the
        # recognizer; the UI surfaces it as an unrecognised-format file (§8.2).
        item = RecognizedFile(
            path.name,
            size,
            "application/octet-stream",
            RecognitionResult(
                recognized=False,
                provider="-",
                note="unsupported file format",
            ),
        )
        _cache_recognition(path, item)
        return item
    mime = _MIME_BY_EXT[ext]
    try:
        content = path.read_bytes()
        result = get_recognizer().recognize(
            content=content, mime_type=mime, filename=path.name
        )
    except RecognizerError as exc:
        # Don't cache transport/config failures — let a retry re-run.
        return RecognizedFile(path.name, size, mime, None, error=str(exc))
    item = RecognizedFile(path.name, size, mime, result)
    _cache_recognition(path, item)
    return item


def recognize_inbox() -> list[RecognizedFile]:
    """Recognise every file currently in the inbox (synchronous)."""
    return [recognize_file(p) for p in list_inbox_files()]


async def _recognize_file_async(recognizer, path: Path) -> RecognizedFile:
    """Async recognition of a single file using the given recognizer. Mirrors
    :func:`recognize_file` (same signature cache) but awaits the recognizer so a
    batch can overlap."""
    cached = cached_recognition(path)
    if cached is not None:
        return cached
    ext = path.suffix.lstrip(".").lower()
    size = path.stat().st_size
    if ext not in _MIME_BY_EXT:
        item = RecognizedFile(
            path.name,
            size,
            "application/octet-stream",
            RecognitionResult(recognized=False, provider="-", note="unsupported file format"),
        )
        _cache_recognition(path, item)
        return item
    mime = _MIME_BY_EXT[ext]
    try:
        content = path.read_bytes()
        result = await recognizer.arecognize(content=content, mime_type=mime, filename=path.name)
    except RecognizerError as exc:
        # Don't cache transport/config failures — let a retry re-run.
        return RecognizedFile(path.name, size, mime, None, error=str(exc))
    item = RecognizedFile(path.name, size, mime, result)
    _cache_recognition(path, item)
    return item


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

    This is the *fully classified* case (recognised + a catalogue type). Other
    readable files still become confirm entries (see :func:`is_confirmable`) but
    are flagged for manual review (see :func:`needs_manual_review`)."""
    return bool(
        item.result
        and item.result.recognized
        and item.result.document_type in KNOWN_FORMAT_TYPES
    )


# MIME types the recognizer can actually read (set form of _MIME_BY_EXT).
_RECOGNIZABLE_MIMES: frozenset[str] = frozenset(_MIME_BY_EXT.values())


def is_confirmable(item: RecognizedFile) -> bool:
    """True when a file should become a confirm-&-edit entry.

    Any file the recognizer could *read* (a supported PDF/JPG/PNG) becomes an
    entry — even when its type couldn't be classified, so the operator can set it
    by hand. Only genuinely unsupported file formats (which were never fed to the
    recognizer) stay flagged in the inbox without an entry (§8.2)."""
    return bool(item.result) and item.mime_type in _RECOGNIZABLE_MIMES


def needs_manual_review(item: RecognizedFile) -> bool:
    """True for a confirmable file the recognizer could NOT classify into a
    catalogue type — the entry is shown but highlighted for manual review."""
    return is_confirmable(item) and not is_known_format(item)


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
    files = list_inbox_files()
    # Forget recognitions for files that have since left the inbox.
    _prune_recognition_cache({p.name for p in files})
    for path in files:
        if path.name in exclude:
            continue
        # recognize_file is cached, so a file already recognised (by the explicit
        # Recognize / Recognize all step, or an earlier gate check) costs nothing.
        if is_trigger(recognize_file(path)):
            return True
    return False
