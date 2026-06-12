"""Apply recognition results to the database and file tree (PRD §8.4–8.6).

Pipeline:
  1. Group files of the same logical document (front/back, pages) into one unit
     (§8.5.4).
  2. Passports first — upsert the driver by ``identification_id`` (§8.4).
  3. Other documents — match an existing driver and attach (§8.5).
  4. Versioning (§8.6): when a newer document supersedes an expired same-type one,
     the expired version's files move to ``Archive/`` and it is marked archived.
     A freshly uploaded already-expired document is still recorded, but as an
     archived document with its files in ``Archive/`` (never a current version).
  5. Saved files move out of ``_Inbox/`` into the driver's folder, named per TZ §3.
"""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from flask import current_app
from sqlalchemy import func

from app.docs.constants import ENTITY_DRIVER
from app.docs.models import DocumentType, DriverDocument, DriverFile
from app.docs.pipeline import PASSPORT_TYPES, TRIGGER_TYPES, RecognizedFile
from app.docs.validation import confirm_field_errors, is_pesel, normalize_passport_number
from app.drivers.models import Driver
from app.extensions import db

_PASSPORT_TYPES = PASSPORT_TYPES

# Filename tokens that distinguish sides/pages of the SAME document (§8.5.4).
_SIDE_TOKENS = {
    "front", "back", "recto", "verso", "side", "page", "pg", "p",
    "strona", "str", "a", "b", "1", "2", "scan", "img",
}


@dataclass
class ApplyReport:
    created_drivers: list[str] = field(default_factory=list)
    updated_drivers: list[str] = field(default_factory=list)
    documents_added: list[str] = field(default_factory=list)
    documents_skipped: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    left_in_inbox: list[dict] = field(default_factory=list)

    def _leave(self, filename: str, reason: str) -> None:
        self.left_in_inbox.append({"filename": filename, "reason": reason})


@dataclass
class _Unit:
    """One logical document = a representative result + its physical files."""

    result: object
    items: list[RecognizedFile]

    @property
    def filenames(self) -> list[str]:
        return [i.filename for i in self.items]


# --- grouping (front/back, §8.5.4) -------------------------------------------


def _normalized_base(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0].lower()
    parts = [p for p in re.split(r"[_\-\s]+", stem) if p and p not in _SIDE_TOKENS]
    return "_".join(parts)


def _person_key(result) -> str:
    if result.identification_id:
        return f"id:{result.identification_id}"
    if result.passport_number:
        return f"pass:{result.passport_number}"
    return f"name:{result.first_name}|{result.last_name}"


def _group_units(
    items: list[RecognizedFile],
    forced: dict[str, uuid.UUID],
    forced_docs: dict[str, uuid.UUID] | None = None,
) -> list[_Unit]:
    """Merge files that describe the same document into one unit (§8.5.4).

    Manual overrides are part of the key so files bound to different drivers
    (``forced``) or to different existing documents (``forced_docs``) never merge.
    """
    forced_docs = forced_docs or {}
    units: dict[tuple, _Unit] = {}
    order: list[tuple] = []
    for item in items:
        r = item.result
        fdu = forced.get(item.filename)
        fdoc = forced_docs.get(item.filename)
        if r.start_date or r.end_date:
            key = (r.document_type, _person_key(r), r.start_date, r.end_date, fdu, fdoc)
        else:
            key = (r.document_type, _person_key(r), _normalized_base(item.filename), fdu, fdoc)
        unit = units.get(key)
        if unit is None:
            units[key] = _Unit(result=r, items=[item])
            order.append(key)
        else:
            unit.items.append(item)
    return [units[k] for k in order]


# --- catalogue / matching helpers --------------------------------------------


def _known_driver_types() -> set[str]:
    rows = db.session.execute(
        db.select(DocumentType.type).where(
            DocumentType.entity_type == ENTITY_DRIVER,
            DocumentType.is_deleted.is_(False),
        )
    ).scalars().all()
    return set(rows)


def _active_driver_query():
    return db.select(Driver).where(Driver.is_deleted.is_(False))


def _ci_contains(column, value: str):
    """Case-insensitive 'contains' predicate: ``lower(column) LIKE '%value%'``.
    The value's LIKE wildcards are escaped so they match literally."""
    safe = (
        value.lower()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return func.lower(column).like(f"%{safe}%", escape="\\")


def _find_driver(result) -> tuple[Driver | None, str | None]:
    """Match a driver per §8.5. Returns (driver, ambiguity_reason)."""
    if result.identification_id:
        d = db.session.execute(
            _active_driver_query().where(Driver.identification_id == result.identification_id)
        ).scalar_one_or_none()
        if d:
            return d, None
    if result.passport_number:
        d = db.session.execute(
            _active_driver_query().where(Driver.passport_number == result.passport_number)
        ).scalar_one_or_none()
        if d:
            return d, None
    if result.first_name and result.last_name:
        # Name tier (only): case-insensitive substring match — the recognised
        # first/last name must appear within the driver's stored names (§8.5).
        matches = db.session.execute(
            _active_driver_query().where(
                _ci_contains(Driver.first_name, result.first_name),
                _ci_contains(Driver.last_name, result.last_name),
            )
        ).scalars().all()
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            # Disambiguate same-name drivers by birth date when the document
            # carries one (§8.5). Collisions on name+birth_date are unlikely.
            if result.birth_date:
                narrowed = [d for d in matches if d.birth_date == result.birth_date]
                if len(narrowed) == 1:
                    return narrowed[0], None
            return None, "ambiguous name match"
    return None, None


def find_matching_driver(result) -> Driver | None:
    """The driver a recognised file matches (per §8.5), or ``None`` when no driver
    matches or the match is ambiguous. Public wrapper over :func:`_find_driver`."""
    if result is None:
        return None
    driver, ambiguity = _find_driver(result)
    return None if ambiguity else driver


def entry_bound_driver(result, selected_driver=None) -> Driver | None:
    """The driver an inbox entry is bound to: the manually selected one
    (``selected_driver`` UUID) if any, else the recognition match. Used to scope
    the 'Bind to document' picker to that driver's documents (§8.5)."""
    if selected_driver:
        driver = db.session.get(Driver, selected_driver)
        if driver is not None and not driver.is_deleted:
            return driver
    return find_matching_driver(result)


def entry_confirm_errors(result, selected_driver=None) -> dict[str, str]:
    """All errors blocking confirmation of an inbox entry (§8.2):

    * the format rules and 'document type required' (:func:`confirm_field_errors`);
    * for a **non-trigger** document (anything other than a passport / technical
      passport), a bound driver is required — it attaches to an existing driver,
      so we must know which one. Passports/tech-passports are exempt: they create
      the entity, so they may be confirmed with no existing driver bound.
    """
    errors = confirm_field_errors(result)
    dt = getattr(result, "document_type", None)
    if dt and dt not in TRIGGER_TYPES and entry_bound_driver(result, selected_driver) is None:
        errors["driver_uuid"] = "Select a driver — this document attaches to an existing driver."
    return errors


def suggest_existing_document(result, selected_driver=None) -> DriverDocument | None:
    """The existing active document a recognised file would attach to: the entry's
    driver (manual bind or recognition match) document of the same type. Used to
    pre-select 'Bind to document' so a same-type document isn't duplicated.
    Returns ``None`` when the type is unknown, no driver is bound/matched, or no
    such document exists — i.e. a new document should be created."""
    if result is None or not getattr(result, "document_type", None):
        return None
    driver = entry_bound_driver(result, selected_driver)
    if driver is None:
        return None
    docs = _active_docs(driver, result.document_type)
    return docs[0] if docs else None


def _active_docs(driver: Driver, doc_type: str) -> list[DriverDocument]:
    return db.session.execute(
        db.select(DriverDocument).where(
            DriverDocument.driver_uuid == driver.uuid,
            DriverDocument.document_type == doc_type,
            DriverDocument.is_deleted.is_(False),
            DriverDocument.archived_at.is_(None),
        )
    ).scalars().all()


# --- file moves (TZ §3 naming) -----------------------------------------------


def _slug(value: str) -> str:
    return "_".join((value or "").split())


def _root() -> Path:
    return Path(current_app.config["DOCUMENTS_DIR"])


def _inbox() -> Path:
    return _root() / current_app.config["DOCUMENTS_INBOX_DIRNAME"]


def _person_folder(driver: Driver) -> Path:
    folder = _root() / "Drivers" / _slug(f"{driver.first_name} {driver.last_name}")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _archive_folder(driver: Driver) -> Path:
    folder = _person_folder(driver) / "Archive"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _dates_token(start: date | None, end: date | None) -> str:
    if start and end:
        return f"{start.isoformat()}_{end.isoformat()}"
    return (end or start).isoformat() if (end or start) else ""


def _unique(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, dot, ext = name.rpartition(".")
    base, suffix = (stem, f".{ext}") if dot else (name, "")
    i = 1
    while (candidate := directory / f"{base}_{i}{suffix}").exists():
        i += 1
    return candidate


def _target_name(driver: Driver, doc_type: str, result, original: str, side: int) -> str:
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    parts = [_slug(f"{driver.first_name} {driver.last_name}"), doc_type]
    token = _dates_token(result.start_date, result.end_date)
    if token:
        parts.append(token)
    if side:  # second+ file of the same document keeps a distinct name
        parts.append(str(side + 1))
    stem = "_".join(p for p in parts if p)
    return f"{stem}.{ext}" if ext else stem


def _move_unit_into_folder(driver: Driver, doc_type: str, unit: _Unit) -> list[str]:
    """Move all of a unit's files into the driver folder. Return relative paths."""
    folder = _person_folder(driver)
    root = _root()
    rels: list[str] = []
    for side, item in enumerate(unit.items):
        source = _inbox() / item.filename
        target = _unique(folder, _target_name(driver, doc_type, unit.result, item.filename, side))
        shutil.move(str(source), str(target))
        rels.append(target.relative_to(root).as_posix())
    return rels


def _move_unit_to_archive(driver: Driver, unit: _Unit) -> list[str]:
    """Move an expired upload's files into Archive/. Return relative paths."""
    folder = _archive_folder(driver)
    root = _root()
    rels: list[str] = []
    for item in unit.items:
        source = _inbox() / item.filename
        target = _unique(folder, item.filename)
        shutil.move(str(source), str(target))
        rels.append(target.relative_to(root).as_posix())
    return rels


def _archive_document(driver: Driver, doc: DriverDocument, now: datetime) -> None:
    """Move a stored document's files to Archive/ and mark it archived (§8.6)."""
    folder = _archive_folder(driver)
    root = _root()
    for file in doc.files:
        source = root / file.file_link
        if source.exists():
            target = _unique(folder, source.name)
            shutil.move(str(source), str(target))
            file.file_link = target.relative_to(root).as_posix()
    doc.archived_at = now


def _add_files(doc: DriverDocument, result, rels: list[str]) -> None:
    """Attach moved files to a document as driver_file rows, each carrying the
    metadata recognised for the document."""
    for rel in rels:
        doc.files.append(
            DriverFile(
                file_link=rel,
                document_type=doc.document_type,
                document_id=result.document_id or None,
                start_date=result.start_date,
                end_date=result.end_date,
            )
        )


# --- driver upsert (§8.4) ----------------------------------------------------


def _upsert_driver_from_passport(result, report: ApplyReport) -> Driver | None:
    driver = db.session.execute(
        _active_driver_query().where(Driver.identification_id == result.identification_id)
    ).scalar_one_or_none()

    passport_number = normalize_passport_number(result.passport_number) or None

    if driver is None:
        if not (result.first_name and result.last_name):
            return None
        driver = Driver(
            first_name=result.first_name,
            last_name=result.last_name,
            identification_id=result.identification_id,
            passport_number=passport_number,
            birth_date=result.birth_date,
            nationality=result.nationality,
        )
        db.session.add(driver)
        db.session.flush()
        report.created_drivers.append(driver.full_name)
        return driver

    changed = False
    updates = {
        "first_name": result.first_name,
        "last_name": result.last_name,
        "passport_number": passport_number,
        "birth_date": result.birth_date,
        "nationality": result.nationality,
    }
    for attr, new in updates.items():
        if new is not None and getattr(driver, attr) != new:
            setattr(driver, attr, new)
            changed = True
    if changed:
        report.updated_drivers.append(driver.full_name)
    return driver


# --- document attach (§8.5.1, §8.5.3–8.5.4, §8.6) ----------------------------


def _attach_to_existing(
    doc: DriverDocument,
    unit: _Unit,
    report: ApplyReport,
) -> tuple[uuid.UUID, str] | None:
    """Attach a unit's files to an existing document the operator picked (§8.5) —
    e.g. an extra scan/page of a document that already exists. Moves the files
    into that document's driver folder and records them as driver_file rows; the
    document's own fields (type/number/dates) are left untouched."""
    driver = doc.driver
    rels = _move_unit_into_folder(driver, doc.document_type, unit)
    _add_files(doc, unit.result, rels)
    report.documents_skipped.append(
        f"{', '.join(unit.filenames)} → attached to existing {doc.document_type}"
    )
    # Tachograph card → keep the driver's number in step with the document.
    if doc.sync_tachograph_number_to_driver(driver):
        report.updated_drivers.append(driver.full_name)
    return driver.uuid, doc.document_type


def _attach_unit(
    driver: Driver,
    unit: _Unit,
    doc_type: str,
    known_types: set[str],
    report: ApplyReport,
    today: date,
    now: datetime,
) -> tuple[uuid.UUID, str] | None:
    """Attach one document unit. Returns (driver_uuid, doc_type) if a current
    document was created/updated (so the caller can run the archive pass)."""
    if doc_type not in known_types:
        for name in unit.filenames:
            report._leave(name, f"type '{doc_type}' not in catalogue")
        return None

    result = unit.result
    existing = _active_docs(driver, doc_type)

    # PESEL document → copy the number onto the driver profile (§8, item 7).
    if doc_type == "pesel" and result.identification_id and is_pesel(result.identification_id):
        if driver.pesel != result.identification_id:
            driver.pesel = result.identification_id
            report.updated_drivers.append(driver.full_name)

    # Already-expired upload (§8.6): still record it, but as an archived document
    # with its files in Archive/ — never a current/controlling version.
    if result.end_date and result.end_date < today:
        rels = _move_unit_to_archive(driver, unit)
        doc = DriverDocument(
            driver_uuid=driver.uuid,
            document_type=doc_type,
            document_id=result.document_id or None,
            start_date=result.start_date,
            end_date=result.end_date,
            archived_at=now,
        )
        db.session.add(doc)
        _add_files(doc, result, rels)
        report.archived.append(f"{driver.full_name}: {doc_type} (expired upload → Archive)")
        return None

    # Dedup / scan merge (§8.5.3–8.5.4): same driver+type+dates.
    same = next(
        (d for d in existing
         if d.start_date == result.start_date and d.end_date == result.end_date),
        None,
    )
    rels = _move_unit_into_folder(driver, doc_type, unit)

    if same is not None:
        _add_files(same, result, rels)
        # Tachograph card → keep the driver's number in step with the document.
        if same.sync_tachograph_number_to_driver(driver):
            report.updated_drivers.append(driver.full_name)
        report.documents_skipped.append(
            f"{', '.join(unit.filenames)} → scan added to existing {doc_type}"
        )
        return driver.uuid, doc_type

    # New document — created so its files have a document_uuid; the driver_file
    # rows are added in the same session/transaction (rule: doc before file).
    doc = DriverDocument(
        driver_uuid=driver.uuid,
        document_type=doc_type,
        document_id=result.document_id or None,
        start_date=result.start_date,
        end_date=result.end_date,
    )
    db.session.add(doc)
    _add_files(doc, result, rels)
    # Tachograph card → the confirmed card number becomes the driver's number.
    if doc.sync_tachograph_number_to_driver(driver):
        report.updated_drivers.append(driver.full_name)
    report.documents_added.append(f"{driver.full_name}: {doc_type} ({len(rels)} file(s))")
    return driver.uuid, doc_type


def _run_archive_pass(affected, report: ApplyReport, today: date, now: datetime) -> None:
    """Archive expired same-type versions superseded by a newer one (§8.6)."""
    drivers: dict = {}
    for driver_uuid, _ in affected:
        drivers.setdefault(driver_uuid, db.session.get(Driver, driver_uuid))

    for driver_uuid, doc_type in affected:
        driver = drivers[driver_uuid]
        docs = [d for d in _active_docs(driver, doc_type) if d.end_date is not None]
        if len(docs) < 2:
            continue
        latest_end = max(d.end_date for d in docs)
        for d in docs:
            if d.end_date < today and d.end_date < latest_end:
                _archive_document(driver, d, now)
                report.archived.append(f"{driver.full_name}: {doc_type} {d.end_date} → Archive")


# --- orchestration -----------------------------------------------------------


def apply_recognized(
    recognized: list[RecognizedFile],
    today: date | None = None,
    forced: dict[str, uuid.UUID] | None = None,
    forced_docs: dict[str, uuid.UUID] | None = None,
) -> ApplyReport:
    """Persist recognised files. Commits once at the end.

    ``forced`` maps a filename to a driver UUID for manual binding (§8.5): such
    files attach directly to that driver, bypassing passport upsert / matching.
    ``forced_docs`` maps a filename to an existing document UUID: such files are
    attached straight to that document (e.g. an extra scan / page of a document
    that already exists), bypassing the create/match logic entirely.
    """
    today = today or date.today()
    now = datetime.now(UTC)
    forced = forced or {}
    forced_docs = forced_docs or {}
    report = ApplyReport()
    known_types = _known_driver_types()
    affected: set[tuple] = set()

    valid: list[RecognizedFile] = []
    for item in recognized:
        r = item.result
        if item.error or not r or not r.recognized:
            report._leave(item.filename, item.error or "not recognized")
            continue
        valid.append(item)

    units = _group_units(valid, forced, forced_docs)
    doc_bound_units = [u for u in units if forced_docs.get(u.items[0].filename)]
    rest = [u for u in units if not forced_docs.get(u.items[0].filename)]
    forced_units = [u for u in rest if forced.get(u.items[0].filename)]
    auto_units = [u for u in rest if not forced.get(u.items[0].filename)]
    passport_units = [u for u in auto_units if (u.result.document_type or "") in _PASSPORT_TYPES]
    other_units = [u for u in auto_units if (u.result.document_type or "") not in _PASSPORT_TYPES]

    # Phase 0 — files manually bound to an existing document attach straight to it.
    for unit in doc_bound_units:
        doc = db.session.get(DriverDocument, forced_docs[unit.items[0].filename])
        if (
            doc is None
            or doc.is_deleted
            or doc.archived_at is not None
            or doc.driver is None
            or doc.driver.is_deleted
        ):
            for name in unit.filenames:
                report._leave(name, "selected document not found")
            continue
        touched = _attach_to_existing(doc, unit, report)
        if touched:
            affected.add(touched)

    # Phase 1 — passports upsert drivers (§8.4), then attach the passport scan.
    for unit in passport_units:
        r = unit.result
        if not r.identification_id:
            for name in unit.filenames:
                report._leave(name, "passport without identification_id")
            continue
        driver = _upsert_driver_from_passport(r, report)
        if driver is None:
            for name in unit.filenames:
                report._leave(name, "cannot create driver (missing name)")
            continue
        touched = _attach_unit(driver, unit, r.document_type, known_types, report, today, now)
        if touched:
            affected.add(touched)

    # Phase 2 — other documents attach to an existing driver (§8.5).
    for unit in other_units:
        r = unit.result
        driver, ambiguity = _find_driver(r)
        if ambiguity:
            for name in unit.filenames:
                report._leave(name, ambiguity)
            continue
        if driver is None:
            for name in unit.filenames:
                report._leave(name, "driver not found (non-passport does not create)")
            continue
        touched = _attach_unit(driver, unit, r.document_type, known_types, report, today, now)
        if touched:
            affected.add(touched)

    # Phase 3 — manually-bound files attach directly to the chosen driver (§8.5).
    for unit in forced_units:
        driver = db.session.get(Driver, forced[unit.items[0].filename])
        if driver is None or driver.is_deleted:
            for name in unit.filenames:
                report._leave(name, "manually selected driver not found")
            continue
        touched = _attach_unit(driver, unit, unit.result.document_type, known_types, report, today, now)
        if touched:
            affected.add(touched)

    _run_archive_pass(affected, report, today, now)

    db.session.commit()
    return report
