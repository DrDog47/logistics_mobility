"""Tests for applying recognition results to DB + file tree (PRD §8.4–8.6)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.docs.constants import ENTITY_DRIVER
from app.docs.models import DocumentType, DriverDocument, DriverFile
from app.docs.persistence import apply_recognized
from app.docs.pipeline import RecognizedFile
from app.docs.services import inbox_dir, resolve_stored_file
from app.drivers.models import Driver
from app.extensions import db
from app.docs.recognizer import RecognitionResult

_MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}


def _setup(app, tmp_path: Path) -> None:
    app.config["DOCUMENTS_DIR"] = str(tmp_path)
    for code in ("passport", "visa"):
        db.session.add(DocumentType(type=code, entity_type=ENTITY_DRIVER, label=code))
    db.session.commit()


def _mk(filename: str, result: RecognitionResult) -> RecognizedFile:
    """Write a physical inbox file and wrap it as a RecognizedFile."""
    (inbox_dir() / filename).write_bytes(b"dummy")
    ext = filename.rsplit(".", 1)[-1].lower()
    return RecognizedFile(filename, 5, _MIME.get(ext, "application/pdf"), result)


def _driver_by_id(id_: str) -> Driver | None:
    return db.session.execute(
        db.select(Driver).where(Driver.identification_id == id_)
    ).scalar_one_or_none()


def test_passport_creates_driver_and_moves_file(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        item = _mk(
            "Ivan_Ivanov_Passport.pdf",
            RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-1",
                first_name="Ivan", last_name="Ivanov", passport_number="PB123",
                birth_date=date(1990, 1, 2), nationality="BLR", end_date=date(2030, 5, 12),
            ),
        )
        report = apply_recognized([item])

        driver = _driver_by_id("ID-1")
        assert driver is not None
        assert driver.full_name == "Ivan Ivanov"
        assert driver.organisation_uuid is None  # filled manually later (§8.4)
        assert driver.passport_number == "PB123"
        assert report.created_drivers == ["Ivan Ivanov"]

        doc = db.session.execute(db.select(DriverDocument)).scalar_one()
        assert doc.document_type == "passport"
        # One driver_file row, tied to this document, carrying the recognised type.
        assert len(doc.files) == 1
        file = doc.files[0]
        assert file.document_uuid == doc.uuid
        assert file.document_type == "passport"
        assert file.end_date == date(2030, 5, 12)
        assert file.file_link.startswith("Drivers/Ivan_Ivanov/")

        # File moved out of inbox into the driver folder.
        assert not (inbox_dir() / "Ivan_Ivanov_Passport.pdf").exists()
        assert (tmp_path / file.file_link).exists()


def test_passport_update_existing_driver(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        apply_recognized([
            _mk("a.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-9",
                first_name="Petr", last_name="Petrov", passport_number="OLD",
            )),
        ])
        report = apply_recognized([
            _mk("b.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-9",
                first_name="Petr", last_name="Petrov", passport_number="NEW",
            )),
        ])
        assert report.created_drivers == []
        assert report.updated_drivers == ["Petr Petrov"]
        assert _driver_by_id("ID-9").passport_number == "NEW"


def test_visa_attaches_to_driver_created_from_passport(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        report = apply_recognized([
            _mk("Jan_Kowalski_Passport.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-2",
                first_name="Jan", last_name="Kowalski",
            )),
            _mk("Jan_Kowalski_Visa.pdf", RecognitionResult(
                recognized=True, document_type="visa",
                first_name="Jan", last_name="Kowalski",
                start_date=date(2024, 6, 1), end_date=date(2030, 6, 1),
            )),
        ])
        types = sorted(d.document_type for d in db.session.execute(
            db.select(DriverDocument)).scalars().all())
        assert types == ["passport", "visa"]
        assert any("visa" in s for s in report.documents_added)


def test_forced_document_attaches_file_to_existing_document(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        # Create a driver + passport document first.
        apply_recognized([
            _mk("Jan_Kowalski_Passport.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-7",
                first_name="Jan", last_name="Kowalski",
            )),
        ])
        doc = db.session.execute(db.select(DriverDocument)).scalar_one()
        assert len(doc.files) == 1

        # An extra scan, manually bound to that existing document.
        extra = _mk("passport_back.png", RecognitionResult(
            recognized=True, document_type="passport",
            first_name="Jan", last_name="Kowalski",
        ))
        report = apply_recognized([extra], forced_docs={"passport_back.png": doc.uuid})

        db.session.refresh(doc)
        assert len(doc.files) == 2  # the extra scan attached, no new document
        assert db.session.execute(db.select(DriverDocument)).scalars().all() == [doc]
        assert any("attached to existing passport" in s for s in report.documents_skipped)
        assert not (inbox_dir() / "passport_back.png").exists()  # moved into folder


def test_suggest_existing_document_matches_driver_and_type(app, tmp_path):
    from app.docs.persistence import entry_bound_driver, suggest_existing_document

    with app.app_context():
        _setup(app, tmp_path)
        apply_recognized([
            _mk("Jan_Kowalski_Passport.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-5",
                first_name="Jan", last_name="Kowalski",
            )),
        ])
        passport = db.session.execute(db.select(DriverDocument)).scalar_one()

        # Same driver + same type → suggests the existing passport.
        same = RecognitionResult(
            recognized=True, document_type="passport", identification_id="ID-5",
            first_name="Jan", last_name="Kowalski",
        )
        assert suggest_existing_document(same) is passport
        assert entry_bound_driver(same) is passport.driver

        # Same driver, different type → no suggestion (create new).
        visa = RecognitionResult(
            recognized=True, document_type="visa", identification_id="ID-5",
            first_name="Jan", last_name="Kowalski",
        )
        assert suggest_existing_document(visa) is None

        # Unknown driver → no driver, no suggestion.
        ghost = RecognitionResult(
            recognized=True, document_type="passport",
            first_name="No", last_name="Body",
        )
        assert entry_bound_driver(ghost) is None
        assert suggest_existing_document(ghost) is None


def test_find_driver_name_match_is_case_insensitive_substring(app, tmp_path):
    from app.docs.persistence import _find_driver

    with app.app_context():
        _setup(app, tmp_path)
        jan = Driver(first_name="Jan", last_name="Kowalski", identification_id="ID-N")
        db.session.add(jan)
        db.session.commit()

        # Lower-case + partial recognised names still match the stored driver.
        d, amb = _find_driver(RecognitionResult(
            recognized=True, document_type="visa", first_name="jan", last_name="kowal",
        ))
        assert d is jan and amb is None

        # A non-matching surname → no match.
        d, amb = _find_driver(RecognitionResult(
            recognized=True, document_type="visa", first_name="jan", last_name="nowak",
        ))
        assert d is None


def test_entry_confirm_errors_driver_required_for_non_triggers(app, tmp_path):
    from app.docs.persistence import entry_confirm_errors

    with app.app_context():
        _setup(app, tmp_path)
        jan = Driver(first_name="Jan", last_name="Kowalski", identification_id="ID-3")
        db.session.add(jan)
        db.session.commit()

        # Non-trigger (visa) with no matching driver → driver is required.
        ghost_visa = RecognitionResult(
            recognized=True, document_type="visa", first_name="No", last_name="Body",
        )
        assert "driver_uuid" in entry_confirm_errors(ghost_visa)

        # Non-trigger with a bound driver → no driver error.
        assert "driver_uuid" not in entry_confirm_errors(ghost_visa, selected_driver=jan.uuid)

        # Passport (trigger) with no driver → exempt, no driver error.
        new_passport = RecognitionResult(
            recognized=True, document_type="passport", first_name="A", last_name="B",
        )
        assert "driver_uuid" not in entry_confirm_errors(new_passport)


def test_forced_document_missing_leaves_file_in_inbox(app, tmp_path):
    import uuid as _uuid

    with app.app_context():
        _setup(app, tmp_path)
        item = _mk("orphan.png", RecognitionResult(
            recognized=True, document_type="passport", first_name="X", last_name="Y",
        ))
        report = apply_recognized([item], forced_docs={"orphan.png": _uuid.uuid4()})
        assert report.left_in_inbox[0]["reason"] == "selected document not found"
        assert (inbox_dir() / "orphan.png").exists()


def test_non_passport_unknown_driver_stays_in_inbox(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        report = apply_recognized([
            _mk("ghost.pdf", RecognitionResult(
                recognized=True, document_type="visa",
                first_name="No", last_name="Body", end_date=date(2027, 1, 1),
            )),
        ])
        assert report.documents_added == []
        assert report.left_in_inbox[0]["filename"] == "ghost.pdf"
        assert (inbox_dir() / "ghost.pdf").exists()  # not moved
        # Rule 2: an unplaced file is NOT recorded as a driver_file.
        assert db.session.execute(db.select(DriverFile)).scalars().all() == []


def test_unknown_type_stays_in_inbox(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        report = apply_recognized([
            _mk("Ann_Lee_Passport.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-3",
                first_name="Ann", last_name="Lee",
            )),
            _mk("Ann_Lee_Adr.pdf", RecognitionResult(
                recognized=True, document_type="adr",  # not seeded in catalogue
                first_name="Ann", last_name="Lee", end_date=date(2028, 1, 1),
            )),
        ])
        assert any("adr" in f["reason"] for f in report.left_in_inbox)
        assert (inbox_dir() / "Ann_Lee_Adr.pdf").exists()


def test_duplicate_document_adds_scan(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        common = dict(
            document_type="visa", first_name="Max", last_name="Mux",
            start_date=date(2024, 1, 1), end_date=date(2030, 1, 1),
        )
        apply_recognized([
            _mk("Max_Mux_Passport.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-4",
                first_name="Max", last_name="Mux",
            )),
        ])
        apply_recognized([_mk("front.png", RecognitionResult(recognized=True, **common))])
        report = apply_recognized([_mk("back.png", RecognitionResult(recognized=True, **common))])

        doc = db.session.execute(
            db.select(DriverDocument).where(DriverDocument.document_type == "visa")
        ).scalar_one()
        assert len(doc.files) == 2  # front + back merged into one document
        assert any("scan added" in s for s in report.documents_skipped)


def test_front_back_grouped_in_one_batch(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        apply_recognized([
            _mk("Lee_Min_Passport.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-5",
                first_name="Lee", last_name="Min",
            )),
            _mk("Lee_Min_Visa_front.png", RecognitionResult(
                recognized=True, document_type="visa", first_name="Lee", last_name="Min",
                start_date=date(2024, 1, 1), end_date=date(2030, 1, 1),
            )),
            _mk("Lee_Min_Visa_back.png", RecognitionResult(
                recognized=True, document_type="visa", first_name="Lee", last_name="Min",
                start_date=date(2024, 1, 1), end_date=date(2030, 1, 1),
            )),
        ])
        visa = db.session.execute(
            db.select(DriverDocument).where(DriverDocument.document_type == "visa")
        ).scalar_one()  # exactly ONE visa document for both sides
        assert len(visa.files) == 2


def test_outdated_version_archived(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        # Stored while still valid (today in early 2025).
        apply_recognized(
            [
                _mk("Old_Han_Passport.pdf", RecognitionResult(
                    recognized=True, document_type="passport", identification_id="ID-6",
                    first_name="Old", last_name="Han",
                )),
                _mk("Old_Han_Visa_2025.pdf", RecognitionResult(
                    recognized=True, document_type="visa", first_name="Old", last_name="Han",
                    end_date=date(2025, 12, 31),
                )),
            ],
            today=date(2025, 1, 1),
        )
        # A newer visa arrives; the 2025 one is now expired and superseded.
        report = apply_recognized(
            [
                _mk("Old_Han_Visa_2027.pdf", RecognitionResult(
                    recognized=True, document_type="visa", first_name="Old", last_name="Han",
                    end_date=date(2027, 12, 31),
                )),
            ],
            today=date(2026, 6, 9),
        )

        docs = db.session.execute(
            db.select(DriverDocument).where(DriverDocument.document_type == "visa")
        ).scalars().all()
        archived = [d for d in docs if d.archived_at is not None]
        active = [d for d in docs if d.archived_at is None]
        assert len(archived) == 1 and archived[0].end_date == date(2025, 12, 31)
        assert len(active) == 1 and active[0].end_date == date(2027, 12, 31)
        assert any("Archive" in s for s in report.archived)
        # Archived file physically moved under .../Archive/.
        archived_link = archived[0].files[0].file_link
        assert "/Archive/" in archived_link
        assert (tmp_path / archived_link).exists()


def test_expired_upload_with_current_recorded_as_archived(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        apply_recognized(
            [
                _mk("Cur_Yi_Passport.pdf", RecognitionResult(
                    recognized=True, document_type="passport", identification_id="ID-7",
                    first_name="Cur", last_name="Yi",
                )),
                _mk("Cur_Yi_Visa_current.pdf", RecognitionResult(
                    recognized=True, document_type="visa", first_name="Cur", last_name="Yi",
                    end_date=date(2030, 1, 1),
                )),
            ],
            today=date(2026, 6, 9),
        )
        report = apply_recognized(
            [
                _mk("Cur_Yi_Visa_old.pdf", RecognitionResult(
                    recognized=True, document_type="visa", first_name="Cur", last_name="Yi",
                    end_date=date(2024, 1, 1),  # already expired
                )),
            ],
            today=date(2026, 6, 9),
        )
        visas = db.session.execute(
            db.select(DriverDocument).where(DriverDocument.document_type == "visa")
        ).scalars().all()
        # Both recorded: the current one + the expired one (now archived).
        assert len(visas) == 2
        archived = [v for v in visas if v.archived_at is not None]
        assert len(archived) == 1 and archived[0].end_date == date(2024, 1, 1)
        assert any("Archive" in s for s in report.archived)
        assert not (inbox_dir() / "Cur_Yi_Visa_old.pdf").exists()  # moved to Archive/
        assert "/Archive/" in archived[0].files[0].file_link


def test_expired_upload_no_current_recorded_as_archived(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        report = apply_recognized(
            [
                _mk("Gap_Bo_Passport.pdf", RecognitionResult(
                    recognized=True, document_type="passport", identification_id="ID-8",
                    first_name="Gap", last_name="Bo",
                )),
                _mk("Gap_Bo_Visa_old.pdf", RecognitionResult(
                    recognized=True, document_type="visa", first_name="Gap", last_name="Bo",
                    end_date=date(2020, 1, 1),  # expired, nothing fresher
                )),
            ],
            today=date(2026, 6, 9),
        )
        # Recorded as an archived document (no longer left in the inbox).
        assert any("Archive" in s for s in report.archived)
        assert not (inbox_dir() / "Gap_Bo_Visa_old.pdf").exists()
        visa = db.session.execute(
            db.select(DriverDocument).where(DriverDocument.document_type == "visa")
        ).scalar_one()
        assert visa.archived_at is not None
        assert visa.files and "/Archive/" in visa.files[0].file_link


def test_forced_driver_binding(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        apply_recognized([
            _mk("Real_Name_Passport.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-10",
                first_name="Real", last_name="Name",
            )),
        ])
        driver = _driver_by_id("ID-10")

        # Visa recognised with a WRONG name, but manually bound to the driver.
        report = apply_recognized(
            [_mk("misc_visa.pdf", RecognitionResult(
                recognized=True, document_type="visa",
                first_name="Wrong", last_name="Person", end_date=date(2030, 1, 1),
            ))],
            forced={"misc_visa.pdf": driver.uuid},
        )
        assert any("visa" in s for s in report.documents_added)
        types = sorted(d.document_type for d in driver.active_documents)
        assert "visa" in types


def test_forced_driver_not_found(app, tmp_path):
    import uuid
    with app.app_context():
        _setup(app, tmp_path)
        report = apply_recognized(
            [_mk("orphan.pdf", RecognitionResult(
                recognized=True, document_type="visa",
                first_name="A", last_name="B", end_date=date(2030, 1, 1),
            ))],
            forced={"orphan.pdf": uuid.uuid4()},
        )
        assert any("not found" in f["reason"] for f in report.left_in_inbox)
        assert (inbox_dir() / "orphan.pdf").exists()


def test_pesel_document_updates_driver_profile(app, tmp_path):
    with app.app_context():
        _setup(app, tmp_path)
        db.session.add(DocumentType(type="pesel", entity_type=ENTITY_DRIVER, label="pesel"))
        db.session.commit()
        apply_recognized([
            _mk("Jan_Kowalski_Passport.pdf", RecognitionResult(
                recognized=True, document_type="passport", identification_id="ID-P",
                first_name="Jan", last_name="Kowalski",
            )),
        ])
        apply_recognized([
            _mk("Jan_Kowalski_Pesel.pdf", RecognitionResult(
                recognized=True, document_type="pesel", identification_id="85010112345",
                first_name="Jan", last_name="Kowalski",
            )),
        ])
        driver = _driver_by_id("ID-P")
        assert driver.pesel == "85010112345"  # copied from the PESEL document


def test_resolve_stored_file_serves_under_root_and_blocks_escape(app, tmp_path):
    with app.app_context():
        app.config["DOCUMENTS_DIR"] = str(tmp_path)
        target = tmp_path / "Drivers" / "Ann_Lee" / "passport.pdf"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"%PDF-1.4")
        # Valid relative path under the root resolves to the real file.
        resolved = resolve_stored_file("Drivers/Ann_Lee/passport.pdf")
        assert resolved is not None and resolved.read_bytes() == b"%PDF-1.4"
        # Traversal, external URLs, and missing files are rejected.
        assert resolve_stored_file("../../etc/passwd") is None
        assert resolve_stored_file("https://example.com/x.pdf") is None
        assert resolve_stored_file("Drivers/Ann_Lee/missing.pdf") is None
        assert resolve_stored_file(None) is None


def _two_namesakes(app, tmp_path) -> None:
    """Two active drivers with the same name but different birth dates."""
    _setup(app, tmp_path)
    apply_recognized([
        _mk("tw1.pdf", RecognitionResult(
            recognized=True, document_type="passport", identification_id="TW-1",
            first_name="Adam", last_name="Nowak", birth_date=date(1985, 3, 4),
        )),
        _mk("tw2.pdf", RecognitionResult(
            recognized=True, document_type="passport", identification_id="TW-2",
            first_name="Adam", last_name="Nowak", birth_date=date(1992, 7, 8),
        )),
    ])


def test_name_match_disambiguated_by_birth_date(app, tmp_path):
    with app.app_context():
        _two_namesakes(app, tmp_path)
        report = apply_recognized([
            _mk("Adam_Nowak_Visa.pdf", RecognitionResult(
                recognized=True, document_type="visa",
                first_name="Adam", last_name="Nowak", birth_date=date(1992, 7, 8),
                end_date=date(2030, 1, 1),
            )),
        ])
        assert any("visa" in s for s in report.documents_added)
        driver = _driver_by_id("TW-2")  # the 1992 namesake
        assert "visa" in [d.document_type for d in driver.active_documents]
        # The other namesake keeps only its own passport — no visa attached.
        assert "visa" not in [d.document_type for d in _driver_by_id("TW-1").active_documents]


def test_name_match_ambiguous_without_birth_date_stays_in_inbox(app, tmp_path):
    with app.app_context():
        _two_namesakes(app, tmp_path)
        report = apply_recognized([
            _mk("Adam_Nowak_Visa.pdf", RecognitionResult(
                recognized=True, document_type="visa",
                first_name="Adam", last_name="Nowak",  # no birth_date → cannot tell
                end_date=date(2030, 1, 1),
            )),
        ])
        assert report.documents_added == []
        assert any("ambiguous" in f["reason"] for f in report.left_in_inbox)
        assert (inbox_dir() / "Adam_Nowak_Visa.pdf").exists()
