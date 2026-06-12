"""Tests for parsing the editable confirmation form (PRD §8.2)."""

from __future__ import annotations

import uuid
from datetime import date

from app.docs.routes import _entries_from_form


def test_entries_from_form_parses_and_skips():
    driver_uuid = uuid.uuid4()
    form = {
        "entry_count": "3",
        "e0_filename": "a.pdf",
        "e0_document_type": "visa",
        "e0_first_name": "Jan",
        "e0_last_name": "Kowalski",
        "e0_end_date": "2030-01-01",
        "e0_driver_uuid": str(driver_uuid),
        "e1_filename": "b.pdf",
        "e1_skip": "on",  # skipped → excluded
        "e2_filename": "",  # no filename → ignored
    }
    recognized, forced, forced_docs = _entries_from_form(form)

    assert [r.filename for r in recognized] == ["a.pdf"]
    r = recognized[0].result
    assert r.document_type == "visa"
    assert r.first_name == "Jan"
    assert r.end_date == date(2030, 1, 1)
    assert r.provider == "manual"
    assert forced == {"a.pdf": driver_uuid}


def test_entries_from_form_empty():
    recognized, forced, forced_docs = _entries_from_form({})
    assert recognized == [] and forced == {} and forced_docs == {}


def test_entries_from_form_ignores_bad_uuid():
    form = {"entry_count": "1", "e0_filename": "x.pdf", "e0_driver_uuid": "not-a-uuid"}
    recognized, forced, forced_docs = _entries_from_form(form)
    assert recognized[0].filename == "x.pdf"
    assert forced == {}


def test_entries_from_form_binds_document():
    doc_uuid = uuid.uuid4()
    form = {
        "e0_filename": "scan.pdf",
        "e0_document_type": "passport",
        "e0_document_uuid": str(doc_uuid),
    }
    recognized, forced, forced_docs = _entries_from_form(form)
    assert recognized[0].filename == "scan.pdf"
    assert forced_docs == {"scan.pdf": doc_uuid}


def test_entries_from_form_scans_opaque_uids():
    # Per-entry saves use hex ids (no entry_count); the parser scans *_filename.
    bind = uuid.uuid4()
    form = {
        "eab12cd_filename": "one.pdf",
        "eab12cd_document_type": "passport",
        "edeadbeef_filename": "two.pdf",
        "edeadbeef_document_type": "visa",
        "edeadbeef_driver_uuid": str(bind),
    }
    recognized, forced, forced_docs = _entries_from_form(form)
    assert sorted(r.filename for r in recognized) == ["one.pdf", "two.pdf"]
    assert {r.result.document_type for r in recognized} == {"passport", "visa"}
    assert forced == {"two.pdf": bind}
