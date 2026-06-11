"""Tests for the document recognizer layer (PRD §8.7)."""

from __future__ import annotations

from datetime import date

import pytest

from app.docs.recognizer import (
    DocumentFieldExtractor,
    IdentificationResult,
    RecognitionResult,
    RecognizerError,
    TwoStageRecognizer,
    build_recognizer,
    get_recognizer,
)
from app.docs.recognizer.fake import FakeRecognizer


def test_fake_parses_tz_named_passport():
    rec = FakeRecognizer()
    result = rec.recognize(
        content=b"",
        mime_type="application/pdf",
        filename="Ivan_Ivanov_Passport_2030-05-12.pdf",
    )
    assert result.recognized is True
    assert result.entity_type == "driver"
    assert result.document_type == "passport"
    assert result.first_name == "Ivan"
    assert result.last_name == "Ivanov"
    assert result.end_date == date(2030, 5, 12)
    assert result.start_date is None
    assert result.provider == "fake"


def test_fake_parses_legalization_with_two_dates():
    rec = FakeRecognizer()
    result = rec.recognize(
        content=b"",
        mime_type="application/pdf",
        filename="Petr_Petrov_Legalization_visa_2024-06-01_2026-06-01.pdf",
    )
    assert result.document_type == "visa"
    assert result.start_date == date(2024, 6, 1)
    assert result.end_date == date(2026, 6, 1)


def test_fake_not_recognized_without_filename():
    rec = FakeRecognizer()
    result = rec.recognize(content=b"x", mime_type="image/png", filename=None)
    assert result.recognized is False
    assert result.confidence == 0.0


def test_to_dict_serialises_dates():
    result = RecognitionResult(recognized=True, end_date=date(2030, 5, 12))
    data = result.to_dict()
    assert data["end_date"] == "2030-05-12"
    assert data["recognized"] is True


def test_build_recognizer_defaults_to_two_stage_fake():
    rec = build_recognizer({"DOCUMENT_RECOGNIZER": "fake"})
    assert isinstance(rec, TwoStageRecognizer)
    assert isinstance(rec._identifier, FakeRecognizer)
    assert isinstance(rec._extractor, FakeRecognizer)
    assert rec.name == "fake+fake"


def test_build_recognizer_unknown_raises():
    with pytest.raises(RecognizerError):
        build_recognizer({"DOCUMENT_RECOGNIZER": "nope"})


def test_build_recognizer_wires_per_stage_claude_models():
    # Provider/model resolution is network-free (the SDK import is lazy).
    rec = build_recognizer(
        {
            "DOCUMENT_IDENTIFIER": "claude",
            "DOCUMENT_EXTRACTOR": "claude",
            "DOCUMENT_IDENTIFIER_MODEL": "claude-haiku-4-5",
            "DOCUMENT_EXTRACTOR_MODEL": "claude-sonnet-4-6",
            "ANTHROPIC_API_KEY": "x",
        }
    )
    assert rec._identifier._model == "claude-haiku-4-5"
    assert rec._extractor._model == "claude-sonnet-4-6"
    assert rec.name == "claude+claude"


def test_recognizer_wired_into_app(app):
    with app.app_context():
        rec = get_recognizer()
        assert rec.name == "fake+fake"


def test_fake_identify_and_extract_round_trip():
    rec = FakeRecognizer()
    filename = "Ivan_Ivanov_Passport_2030-05-12.pdf"
    ident = rec.identify(content=b"", mime_type="application/pdf", filename=filename)
    assert isinstance(ident, IdentificationResult)
    assert ident.recognized is True
    assert ident.document_type == "passport"
    assert ident.entity_type == "driver"

    extracted = rec.extract(
        content=b"",
        mime_type="application/pdf",
        document_type=ident.document_type,
        entity_type=ident.entity_type,
        filename=filename,
    )
    assert extracted.first_name == "Ivan"
    assert extracted.last_name == "Ivanov"
    assert extracted.end_date == date(2030, 5, 12)
    assert extracted.document_type == "passport"


def test_two_stage_matches_single_pass_over_fakes():
    fake = FakeRecognizer()
    two_stage = TwoStageRecognizer(fake, fake)
    filename = "Petr_Petrov_Legalization_visa_2024-06-01_2026-06-01.pdf"
    result = two_stage.recognize(
        content=b"", mime_type="application/pdf", filename=filename
    )
    assert result.recognized is True
    assert result.document_type == "visa"
    assert result.start_date == date(2024, 6, 1)
    assert result.end_date == date(2026, 6, 1)
    assert result.provider == "fake+fake"


def test_two_stage_short_circuits_when_identification_blank():
    class _BlankIdentifier:
        name = "blank"

        def identify(self, *, content, mime_type, filename=None):
            return IdentificationResult(
                recognized=False, note="cannot classify", provider="blank"
            )

    class _ExplodingExtractor(DocumentFieldExtractor):
        name = "boom"

        def extract(self, *, content, mime_type, document_type, entity_type=None, filename=None):
            raise AssertionError("extractor must not run when identification is blank")

    two_stage = TwoStageRecognizer(_BlankIdentifier(), _ExplodingExtractor())
    result = two_stage.recognize(content=b"x", mime_type="image/png", filename=None)
    assert result.recognized is False
    assert result.note == "cannot classify"
    assert result.provider == "blank"
