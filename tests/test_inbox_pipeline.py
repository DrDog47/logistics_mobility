"""Tests for the inbox recognition pipeline (PRD §8.2)."""

from __future__ import annotations

from pathlib import Path

from app.documents.pipeline import (
    RecognizedFile,
    inbox_has_pending_trigger,
    is_trigger,
    list_inbox_files,
    passport_count,
    pending_trigger_count,
    recognize_file,
    recognize_inbox,
    sort_passports_first,
    sort_triggers_first,
)
from app.documents.recognizer import RecognitionResult
from app.documents.services import inbox_dir


def _rf(name: str, doc_type: str | None) -> RecognizedFile:
    return RecognizedFile(
        name, 0, "application/pdf",
        RecognitionResult(recognized=True, document_type=doc_type),
    )


def _seed_inbox(app, tmp_path: Path, *names: str) -> None:
    app.config["DOCUMENTS_DIR"] = str(tmp_path)
    inbox = inbox_dir()
    for name in names:
        (inbox / name).write_bytes(b"%PDF-1.4 dummy")


def test_list_inbox_filters_extensions(app, tmp_path):
    with app.app_context():
        _seed_inbox(
            app,
            tmp_path,
            "Ivan_Ivanov_Passport_2030-05-12.pdf",
            "notes.txt",  # unsupported — skipped
            ".hidden.pdf",  # hidden — skipped
        )
        names = sorted(p.name for p in list_inbox_files())
        assert names == ["Ivan_Ivanov_Passport_2030-05-12.pdf"]


def test_recognize_inbox_with_fake(app, tmp_path):
    with app.app_context():
        _seed_inbox(
            app,
            tmp_path,
            "Ivan_Ivanov_Passport_2030-05-12.pdf",
            "Petr_Petrov_Legalization_visa_2024-06-01_2026-06-01.pdf",
        )
        results = recognize_inbox()
        assert len(results) == 2

        by_name = {r.filename: r for r in results}
        passport = by_name["Ivan_Ivanov_Passport_2030-05-12.pdf"]
        assert passport.error is None
        assert passport.result.recognized is True
        assert passport.result.document_type == "passport"
        assert passport.result.first_name == "Ivan"

        visa = by_name["Petr_Petrov_Legalization_visa_2024-06-01_2026-06-01.pdf"]
        assert visa.result.document_type == "visa"
        assert visa.mime_type == "application/pdf"


def test_recognize_inbox_empty(app, tmp_path):
    with app.app_context():
        app.config["DOCUMENTS_DIR"] = str(tmp_path)
        assert recognize_inbox() == []


def test_sort_passports_first_is_stable():
    items = [
        _rf("visa.pdf", "visa"),
        _rf("p1.pdf", "passport"),
        _rf("medical.pdf", "medical"),
        _rf("p2.pdf", "passport_eu"),
        _rf("license.pdf", "license"),
    ]
    ordered = [r.filename for r in sort_passports_first(items)]
    assert ordered == ["p1.pdf", "p2.pdf", "visa.pdf", "medical.pdf", "license.pdf"]
    assert passport_count(items) == 2


def test_triggers_include_passports_and_tech_passports_first():
    items = [
        _rf("visa.pdf", "visa"),
        _rf("p1.pdf", "passport"),
        _rf("reg.pdf", "tech_passport"),   # vehicle registration certificate
        _rf("p2.pdf", "passport_eu"),
        _rf("insurance.pdf", "insurance"),
    ]
    ordered = [r.filename for r in sort_triggers_first(items)]
    # passports + technical passport float up; everything else keeps its order
    assert ordered == ["p1.pdf", "reg.pdf", "p2.pdf", "visa.pdf", "insurance.pdf"]
    assert pending_trigger_count(items) == 3
    assert is_trigger(_rf("x", "tech_passport")) is True
    assert is_trigger(_rf("x", "insurance")) is False


def test_inbox_has_pending_trigger(app, tmp_path):
    with app.app_context():
        _seed_inbox(
            app,
            tmp_path,
            "Volvo_WB1234A_TechPassport.pdf",                 # vehicle trigger
            "Volvo_WB1234A_Insurance_2025-01-01_2026-01-01.pdf",
        )
        # A trigger (the technical passport) is still waiting.
        assert inbox_has_pending_trigger() is True
        # Excluding the trigger itself, nothing else is a trigger.
        assert inbox_has_pending_trigger(exclude={"Volvo_WB1234A_TechPassport.pdf"}) is False


def test_recognize_file_single(app, tmp_path):
    with app.app_context():
        _seed_inbox(app, tmp_path, "Ivan_Ivanov_Passport_2030-05-12.pdf")
        path = list_inbox_files()[0]
        rf = recognize_file(path)
        assert rf.filename == "Ivan_Ivanov_Passport_2030-05-12.pdf"
        assert rf.error is None
        assert rf.result.document_type == "passport"
