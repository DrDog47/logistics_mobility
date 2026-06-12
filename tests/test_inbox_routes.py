"""Route-level tests for the inbox recognition / review flow (PRD §8.2).

Covers the behaviours added for one-by-one recognition, unrecognised-format
handling, live re-validation, file preview and accepting unrecognised uploads.
"""

from __future__ import annotations

import io
from pathlib import Path

from app.docs.services import inbox_dir
from app.extensions import db
from app.models.user import Role, User


def _admin_login(app, client) -> None:
    user = User(login="admin", email="a@b.c", full_name="Admin", role=Role.ADMIN)
    user.set_password("password1")
    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as s:
        s["_user_id"] = str(user.id)


def _seed_inbox(app, tmp_path: Path, *names: str) -> None:
    app.config["DOCUMENTS_DIR"] = str(tmp_path)
    inbox = inbox_dir()
    for name in names:
        (inbox / name).write_bytes(b"%PDF-1.4 dummy")


def test_recognize_one_known_format_returns_entry(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Ivan_Ivanov_Passport_2030-05-12.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "Ivan_Ivanov_Passport_2030-05-12.pdf"},
        )
        assert resp.status_code == 200
        assert resp.headers["X-Doc-Format"] == "recognized"
        assert b"dz-entry" in resp.data  # a confirmation entry was rendered


def test_recognize_all_returns_entries_concurrently(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(
            app, tmp_path,
            "Ivan_Ivanov_Passport_2030-05-12.pdf",
            "Petr_Petrov_Visa_2024-06-01_2026-06-01.pdf",
        )
        # "Recognize all" → one server request that recognises the batch via
        # asyncio.gather and returns all confirmation entries together.
        resp = client.post("/documents/inbox/recognize", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Ivan_Ivanov_Passport_2030-05-12.pdf" in body
        assert "Petr_Petrov_Visa_2024-06-01_2026-06-01.pdf" in body
        assert "dz-entry" in body


def test_recognize_one_unrecognized_format_is_flagged_not_entered(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        # Recognisable file type, but the filename carries no PRD document type.
        _seed_inbox(app, tmp_path, "random_scan.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "random_scan.pdf"},
        )
        assert resp.status_code == 200
        assert resp.headers["X-Doc-Format"] == "unrecognized"
        assert resp.data == b""  # no entry — the file stays in the inbox, flagged


def test_recognize_one_rejects_unknown_filename(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Ivan_Ivanov_Passport_2030-05-12.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "../../etc/passwd"},
        )
        assert resp.status_code == 404


def test_inbox_lists_unsupported_file_flagged(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "notes.txt")
        resp = client.get("/documents/inbox")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "notes.txt" in body
        # Unknown file type → flagged yellow with a warning, kept in the inbox…
        assert "is-unrecognized" in body
        assert "unknown file type" in body
        # …but recognition is still allowed (the Recognize control is present).
        assert "data-recognize-one" in body


def test_validate_entry_turns_green_when_valid(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        form = {
            "eabc_filename": "Ivan_Ivanov_Passport.pdf",
            "eabc_document_type": "passport",
            "eabc_identification_id": "AB123456",  # latin alnum — valid
            "eabc_nationality": "POL",
        }
        resp = client.post("/documents/inbox/validate", data=form)
        assert resp.status_code == 200
        assert b"dz-entry--ok" in resp.data
        assert b"dz-entry--error" not in resp.data
        # Every cell valid → the submit gate is lifted (no "fix fields" hint).
        assert b"Fix the highlighted fields before saving" not in resp.data


def test_validate_entry_groups_and_roundtrips_new_fields(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        form = {
            "eabc_filename": "Ivan_Ivanov_Passport.pdf",
            "eabc_document_type": "passport",
            "eabc_pesel": "90010112345",                 # form-only field
            "eabc_document_nationality": "POL",          # form-only field
        }
        resp = client.post("/documents/inbox/validate", data=form)
        body = resp.data.decode()
        assert resp.status_code == 200
        # Both groups are rendered.
        assert "Driver fields" in body
        assert "Document" in body
        # Form-only fields survive the re-validation round-trip (not lost).
        assert 'name="eabc_pesel" value="90010112345"' in body
        assert 'name="eabc_document_nationality"' in body
        assert 'value="POL"' in body


def test_validate_entry_stays_red_when_invalid(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        form = {
            "eabc_filename": "Ivan_Ivanov_Passport.pdf",
            "eabc_document_type": "passport",
            "eabc_nationality": "Polska",  # not ISO alpha-3 — invalid
        }
        resp = client.post("/documents/inbox/validate", data=form)
        assert resp.status_code == 200
        assert b"dz-entry--error" in resp.data
        assert b"dz-entry--ok" not in resp.data
        # An edited-but-invalid document must not be submittable (req: stays in
        # review until the failing cell is fixed).
        assert b"Fix the highlighted fields before saving" in resp.data


def test_preview_serves_inbox_file(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Ivan_Ivanov_Passport.pdf")
        resp = client.get(
            "/documents/inbox/preview",
            query_string={"filename": "Ivan_Ivanov_Passport.pdf"},
        )
        assert resp.status_code == 200
        assert resp.data.startswith(b"%PDF")


def test_preview_serves_cyrillic_filename(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Иван_Иванов_Passport.pdf")
        resp = client.get(
            "/documents/inbox/preview",
            query_string={"filename": "Иван_Иванов_Passport.pdf"},
        )
        # Non-ASCII download name must not break header encoding (latin-1).
        assert resp.status_code == 200
        assert resp.data.startswith(b"%PDF")


def test_recognize_one_cyrillic_filename(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Иван_Иванов_Passport_2030-05-12.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "Иван_Иванов_Passport_2030-05-12.pdf"},
        )
        # No non-ASCII response header should be emitted (would crash the server).
        assert resp.status_code == 200
        assert resp.headers["X-Doc-Format"] == "recognized"
        assert "X-Doc-Filename" not in resp.headers


def test_preview_rejects_traversal(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Ivan_Ivanov_Passport.pdf")
        resp = client.get(
            "/documents/inbox/preview",
            query_string={"filename": "../../secrets.env"},
        )
        assert resp.status_code == 404


def test_driver_documents_render_expandable_table(app, client, tmp_path):
    from datetime import date

    from app.docs.constants import ENTITY_DRIVER
    from app.docs.models import DocumentType, DriverDocument, DriverFile
    from app.drivers.models import Driver

    with app.app_context():
        _admin_login(app, client)
        db.session.add(
            DocumentType(type="passport", entity_type=ENTITY_DRIVER, label="Passport (non-EU)")
        )
        driver = Driver(first_name="Jan", last_name="Kowalski", identification_id="J1")
        db.session.add(driver)
        db.session.commit()
        doc = DriverDocument(
            driver_uuid=driver.uuid, document_type="passport", document_id="HB3671261",
            start_date=date(2023, 8, 10), end_date=date(2033, 8, 10),
        )
        db.session.add(doc)
        db.session.commit()
        db.session.add(DriverFile(document_uuid=doc.uuid, file_link="Drivers/Jan/passport.pdf"))
        db.session.commit()

        resp = client.get(f"/drivers/{driver.uuid}")
        body = resp.data.decode()
        assert resp.status_code == 200
        assert "docs-table" in body                         # new expandable table
        assert f'data-doc-toggle="{doc.uuid}"' in body       # row expands its files
        assert 'class="status-pill' in body                  # expiry status pill
        assert "HB3671261" in body                           # document number
        assert "file-card" in body and "passport.pdf" in body  # file card with name
        assert "Passport (non-EU)" in body                   # human type label


def test_driver_page_has_summary_and_tabbed_vacations(app, client, tmp_path):
    from app.drivers.models import Driver

    with app.app_context():
        _admin_login(app, client)
        driver = Driver(first_name="Jan", last_name="Kowalski", identification_id="J1")
        db.session.add(driver)
        db.session.commit()
        resp = client.get(f"/drivers/{driver.uuid}")
        body = resp.data.decode()
        assert resp.status_code == 200
        # Top row: profile + document summary card.
        assert "driver-top" in body
        assert "summary-table" in body
        assert "completeness-bar" in body
        # Tabbed work area with the vacations panel moved into its own tab.
        assert 'data-tabs' in body
        assert 'data-tab-target="vacations"' in body
        assert 'data-tab-panel="vacations"' in body
        assert "driver-vacations-panel" in body  # the vacation panel lives in the tab


def test_driver_page_embeds_inbox_workflow(app, client, tmp_path):
    from app.drivers.models import Driver

    with app.app_context():
        _admin_login(app, client)
        driver = Driver(first_name="Jan", last_name="Kowalski", identification_id="J1")
        db.session.add(driver)
        db.session.commit()
        resp = client.get(f"/drivers/{driver.uuid}")
        assert resp.status_code == 200
        body = resp.data.decode()
        # The inbox workflow is embedded under the drop zone, same as /inbox
        # (assert on untranslated ids/attrs — UI strings are i18n'd).
        assert 'id="inbox-files"' in body
        assert 'id="confirm-entries"' in body
        assert 'id="inbox-spinner"' in body
        # The drop zone refreshes inline instead of navigating to the inbox.
        assert 'data-inbox-refresh="1"' in body


def test_safe_inbox_filename_preserves_unicode():
    from app.docs.services import safe_inbox_filename

    # Cyrillic and Polish letters survive (recogniser parses names from these).
    assert safe_inbox_filename("Иван_Иванов_Passport_2030-05-12.pdf") == (
        "Иван_Иванов_Passport_2030-05-12.pdf"
    )
    assert safe_inbox_filename("Łukasz Kowalski Passport.pdf") == (
        "Łukasz_Kowalski_Passport.pdf"  # spaces → underscores, letters kept
    )


def test_safe_inbox_filename_blocks_traversal_and_junk():
    from app.docs.services import safe_inbox_filename

    assert safe_inbox_filename("../../etc/passwd") == "passwd"
    assert safe_inbox_filename("a/b\\c.pdf") == "c.pdf"
    assert safe_inbox_filename(".hidden.pdf") == "hidden.pdf"
    assert safe_inbox_filename("..") is None
    assert safe_inbox_filename("   ") is None
    assert safe_inbox_filename(None) is None


def test_upload_keeps_cyrillic_filename(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        app.config["DOCUMENTS_DIR"] = str(tmp_path)
        data = {"files": (io.BytesIO(b"%PDF-1.4"), "Иван_Иванов_Passport.pdf")}
        resp = client.post(
            "/documents/upload", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 200
        assert resp.get_json()["saved"] == ["Иван_Иванов_Passport.pdf"]
        assert (inbox_dir() / "Иван_Иванов_Passport.pdf").exists()


def test_discard_removes_file_and_entry(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Ivan_Ivanov_Passport.pdf")
        resp = client.post(
            "/documents/inbox/discard",
            data={"filename": "Ivan_Ivanov_Passport.pdf"},
        )
        assert resp.status_code == 200
        assert resp.data == b""  # entry swapped out
        assert resp.headers["HX-Trigger"] == "inboxChanged"
        assert not (inbox_dir() / "Ivan_Ivanov_Passport.pdf").exists()


def test_recognized_entry_lists_existing_documents_to_bind(app, client, tmp_path):
    from app.docs.constants import ENTITY_DRIVER
    from app.docs.models import DocumentType, DriverDocument
    from app.drivers.models import Driver
    from app.extensions import db

    with app.app_context():
        _admin_login(app, client)
        db.session.add(DocumentType(type="passport", entity_type=ENTITY_DRIVER, label="P"))
        driver = Driver(first_name="Jan", last_name="Kowalski", identification_id="ID9")
        db.session.add(driver)
        db.session.commit()
        db.session.add(DriverDocument(
            driver_uuid=driver.uuid, document_type="passport", document_id="PB1"
        ))
        db.session.commit()

        _seed_inbox(app, tmp_path, "Jan_Kowalski_Passport_2030-05-12.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "Jan_Kowalski_Passport_2030-05-12.pdf"},
        )
        body = resp.data.decode()
        assert "_document_uuid" in body                # Bind to document select present
        assert "passport · PB1" in body               # the driver's existing doc listed
        assert "— new document —" in body             # driver known → new-doc option shown


def test_bind_document_prompts_to_select_driver_when_unrecognised(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Nobody_Unknown_Passport_2030-01-01.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "Nobody_Unknown_Passport_2030-01-01.pdf"},
        )
        body = resp.data.decode()
        # No driver matches → the bind-document picker is empty (driver + type
        # both required) and offers no document to attach to.
        assert "— select a driver and document type first —" in body
        assert "— new document —" not in body


def test_bind_document_scoped_to_recognised_driver(app, client, tmp_path):
    from app.docs.constants import ENTITY_DRIVER
    from app.docs.models import DocumentType, DriverDocument
    from app.drivers.models import Driver
    from app.extensions import db

    with app.app_context():
        _admin_login(app, client)
        db.session.add(DocumentType(type="passport", entity_type=ENTITY_DRIVER, label="P"))
        # Two drivers; only Jan should appear in Jan's entry.
        jan = Driver(first_name="Jan", last_name="Kowalski", identification_id="J1")
        other = Driver(first_name="Petr", last_name="Petrov", identification_id="P1")
        db.session.add_all([jan, other])
        db.session.commit()
        db.session.add(DriverDocument(
            driver_uuid=jan.uuid, document_type="passport", document_id="JANDOC"
        ))
        db.session.add(DriverDocument(
            driver_uuid=other.uuid, document_type="passport", document_id="PETRDOC"
        ))
        db.session.commit()

        _seed_inbox(app, tmp_path, "Jan_Kowalski_Passport_2030-05-12.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "Jan_Kowalski_Passport_2030-05-12.pdf"},
        )
        body = resp.data.decode()
        assert "JANDOC" in body          # the recognised driver's document
        assert "PETRDOC" not in body     # another driver's document is not offered


def test_bind_to_driver_autofilled_on_unique_name_match(app, client, tmp_path):
    from app.docs.constants import ENTITY_DRIVER
    from app.docs.models import DocumentType
    from app.drivers.models import Driver
    from app.extensions import db

    with app.app_context():
        _admin_login(app, client)
        db.session.add(DocumentType(type="passport", entity_type=ENTITY_DRIVER, label="P"))
        jan = Driver(first_name="Jan", last_name="Kowalski", identification_id="J1")
        db.session.add(jan)
        db.session.commit()
        jan_uuid = jan.uuid

        _seed_inbox(app, tmp_path, "Jan_Kowalski_Passport_2030-05-12.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "Jan_Kowalski_Passport_2030-05-12.pdf"},
        )
        body = resp.data.decode()
        # The matched driver is pre-selected in 'Bind to driver'.
        assert f'value="{jan_uuid}" selected' in body


def test_bind_to_driver_not_autofilled_when_no_match(app, client, tmp_path):
    from app.drivers.models import Driver
    from app.extensions import db

    with app.app_context():
        _admin_login(app, client)
        other = Driver(first_name="Other", last_name="Person", identification_id="O1")
        db.session.add(other)
        db.session.commit()
        other_uuid = other.uuid

        _seed_inbox(app, tmp_path, "Nobody_Unknown_Passport_2030-01-01.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "Nobody_Unknown_Passport_2030-01-01.pdf"},
        )
        body = resp.data.decode()
        # No driver matches the recognised name → none is pre-selected.
        assert f'value="{other_uuid}" selected' not in body


def test_non_trigger_requires_bound_driver(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        form = {
            "eabc_filename": "visa.pdf",
            "eabc_document_type": "visa",      # non-trigger document
            "eabc_first_name": "Ghost",        # no such driver exists
            "eabc_last_name": "Person",
        }
        resp = client.post("/documents/inbox/validate", data=form)
        body = resp.data.decode()
        assert "dz-entry--error" in body                       # blocked
        assert "this document attaches to an existing driver" in body
        assert "Fix the highlighted fields before saving" in body


def test_bind_document_filtered_by_document_type(app, client, tmp_path):
    from app.docs.constants import ENTITY_DRIVER
    from app.docs.models import DocumentType, DriverDocument
    from app.drivers.models import Driver
    from app.extensions import db

    with app.app_context():
        _admin_login(app, client)
        for code in ("passport", "visa"):
            db.session.add(DocumentType(type=code, entity_type=ENTITY_DRIVER, label=code))
        jan = Driver(first_name="Jan", last_name="Kowalski", identification_id="J1")
        db.session.add(jan)
        db.session.commit()
        db.session.add(DriverDocument(
            driver_uuid=jan.uuid, document_type="passport", document_id="PASS1"
        ))
        db.session.add(DriverDocument(
            driver_uuid=jan.uuid, document_type="visa", document_id="VISA1"
        ))
        db.session.commit()

        # A recognised visa for Jan should only offer Jan's visa docs, not passport.
        form = {
            "eabc_filename": "Jan_Kowalski_Visa.pdf",
            "eabc_document_type": "visa",
            "eabc_first_name": "Jan",
            "eabc_last_name": "Kowalski",
        }
        resp = client.post("/documents/inbox/validate", data=form)
        body = resp.data.decode()
        assert "VISA1" in body          # same-type document offered
        assert "PASS1" not in body      # other-type document filtered out
        assert "— new document —" in body


def test_validate_preselects_driver_on_name_match(app, client, tmp_path):
    from app.drivers.models import Driver
    from app.extensions import db

    with app.app_context():
        _admin_login(app, client)
        jan = Driver(first_name="Jan", last_name="Kowalski", identification_id="J1")
        db.session.add(jan)
        db.session.commit()
        jan_uuid = jan.uuid

        # Reviewer typed a first+last name matching Jan, no explicit driver bind.
        form = {
            "eabc_filename": "doc.pdf",
            "eabc_document_type": "visa",
            "eabc_first_name": "Jan",
            "eabc_last_name": "Kowalski",
        }
        resp = client.post("/documents/inbox/validate", data=form)
        body = resp.data.decode()
        assert f'value="{jan_uuid}" selected' in body          # Jan pre-selected
        assert "this document attaches to an existing driver" not in body  # not blocked


def test_validate_preselects_existing_document_after_edit(app, client, tmp_path):
    from app.docs.constants import ENTITY_DRIVER
    from app.docs.models import DocumentType, DriverDocument
    from app.drivers.models import Driver
    from app.extensions import db

    with app.app_context():
        _admin_login(app, client)
        db.session.add(DocumentType(type="visa", entity_type=ENTITY_DRIVER, label="V"))
        jan = Driver(first_name="Jan", last_name="Kowalski", identification_id="J1")
        db.session.add(jan)
        db.session.commit()
        visa = DriverDocument(driver_uuid=jan.uuid, document_type="visa", document_id="VISA1")
        db.session.add(visa)
        db.session.commit()
        visa_uuid = visa.uuid

        # Reviewer edits name+type to a driver who already has a visa, with no
        # explicit document bound → the existing visa is preselected.
        form = {
            "eabc_filename": "doc.pdf",
            "eabc_document_type": "visa",
            "eabc_first_name": "Jan",
            "eabc_last_name": "Kowalski",
        }
        resp = client.post("/documents/inbox/validate", data=form)
        body = resp.data.decode()
        assert f'value="{visa_uuid}" selected' in body


def test_confirm_requires_document_type(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        form = {
            "eabc_filename": "mystery.pdf",
            "eabc_document_type": "",          # type not identified
            "eabc_first_name": "Jan",
        }
        resp = client.post("/documents/inbox/validate", data=form)
        body = resp.data.decode()
        assert "dz-entry--error" in body                       # card stays red
        assert "Select the document type." in body             # type error shown
        assert "Fix the highlighted fields before saving" in body  # submit blocked


def test_recognized_entry_shows_thumb_and_delete(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        _seed_inbox(app, tmp_path, "Ivan_Ivanov_Passport_2030-05-12.pdf")
        resp = client.post(
            "/documents/inbox/recognize-one",
            data={"filename": "Ivan_Ivanov_Passport_2030-05-12.pdf"},
        )
        body = resp.data.decode()
        assert "doc-thumb" in body  # preview tile rendered to the left
        assert "/documents/inbox/discard" in body  # delete button present


def test_upload_accepts_unrecognised_format(app, client, tmp_path):
    with app.app_context():
        _admin_login(app, client)
        app.config["DOCUMENTS_DIR"] = str(tmp_path)
        data = {"files": (io.BytesIO(b"hello"), "memo.docx")}
        resp = client.post(
            "/documents/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["saved_count"] == 1
        assert payload["rejected_count"] == 0
        assert (inbox_dir() / "memo.docx").exists()
