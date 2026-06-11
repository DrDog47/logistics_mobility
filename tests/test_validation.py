"""Tests for document identifier format rules (PRD §8)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.docs.validation import normalize_passport_number, validate_recognition


def _r(**kw):
    base = dict(
        document_type=None, identification_id=None, passport_number=None,
        document_id=None, nationality=None, start_date=None, end_date=None,
        birth_date=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_passport_number_normalised_drops_whitespace():
    assert normalize_passport_number("AB 123 4567") == "AB1234567"
    assert normalize_passport_number("  ") is None
    assert normalize_passport_number(None) is None


def test_identification_id_latin_alnum():
    assert validate_recognition(_r(document_type="passport", identification_id="AB123")) == {}
    assert "identification_id" in validate_recognition(
        _r(document_type="passport", identification_id="AB 12")  # space
    )
    assert "identification_id" in validate_recognition(
        _r(document_type="passport", identification_id="АБ12")  # Cyrillic
    )


def test_pesel_must_be_11_digits():
    assert validate_recognition(_r(document_type="pesel", identification_id="85010112345")) == {}
    assert "identification_id" in validate_recognition(
        _r(document_type="pesel", identification_id="850101")  # too short
    )
    assert "identification_id" in validate_recognition(
        _r(document_type="pesel", identification_id="8501011234X")  # has a letter
    )


def test_passport_number_latin_alnum_after_normalising():
    # Spaces are stripped before the check, so a spaced series+number is OK.
    assert validate_recognition(_r(document_type="visa", passport_number="AB 1234567")) == {}
    assert "passport_number" in validate_recognition(
        _r(document_type="visa", passport_number="АБ123")  # Cyrillic
    )


def test_licence_and_tacho_document_id():
    assert validate_recognition(_r(document_type="license", document_id="ABC123")) == {}
    assert "document_id" in validate_recognition(_r(document_type="license", document_id="AB-12"))
    assert validate_recognition(_r(document_type="tacho_card", document_id="A1B2C3D4E5F6G7H8")) == {}
    assert "document_id" in validate_recognition(_r(document_type="tacho_card", document_id="SHORT"))


def test_nationality_and_dates_and_birth():
    assert "nationality" in validate_recognition(_r(document_type="passport", nationality="Pol"))
    assert validate_recognition(_r(document_type="passport", nationality="POL")) == {}
    assert "end_date" in validate_recognition(
        _r(document_type="visa", start_date=date(2030, 1, 1), end_date=date(2026, 1, 1))
    )
    assert "birth_date" in validate_recognition(
        _r(document_type="passport", birth_date=date(2999, 1, 1))
    )
