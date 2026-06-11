"""Offline recognizer — no network, no API key.

Default adapter for dev/tests. It does NOT look inside the file; it parses the
filename per the TZ naming convention ``<First>_<Last>_<Type>_<dates>.<ext>``
(see TZ §3) and returns a best-effort, low-confidence result. Useful because the
inbox files are already named that way; swap in a real LLM adapter for content.
"""

from __future__ import annotations

import re
from datetime import date

from app.docs.recognizer.base import (
    DocumentFieldExtractor,
    DocumentIdentifier,
    DocumentRecognizer,
    IdentificationResult,
    RecognitionResult,
)

# Map filename type-words (lower-case) to catalogue codes (§8.5.1).
_TYPE_WORDS: dict[str, str] = {
    "passport": "passport",
    "passporteu": "passport_eu",
    "techpassport": "tech_passport",   # vehicle registration certificate (trigger)
    "visa": "visa",
    "legalizationvisa": "visa",
    "residence": "residence",
    "legalizationresidence": "residence",
    "license": "license",
    "code95": "code95",
    "medical": "medical",
    "psychological": "psychological",
    "tacho": "tacho_card",
    "tachocard": "tacho_card",
    "adr": "adr",
    "pesel": "pesel",
    "oswiadczenie": "oswiadczenie",
    "employment": "employment",
}

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_dates(text: str) -> tuple[date | None, date | None]:
    """Return (start, end). One date → end only; two → (start, end)."""
    found = _DATE_RE.findall(text)
    parsed: list[date] = []
    for token in found:
        try:
            parsed.append(date.fromisoformat(token))
        except ValueError:
            continue
    if not parsed:
        return None, None
    if len(parsed) == 1:
        return None, parsed[0]
    return parsed[0], parsed[1]


class FakeRecognizer(DocumentRecognizer, DocumentIdentifier, DocumentFieldExtractor):
    """Filename-based stub (no file content is read).

    Implements all three ports off the same filename parse: the single-call
    :meth:`recognize`, plus the two-stage :meth:`identify` / :meth:`extract` — so
    one instance can serve both stages of :class:`TwoStageRecognizer` in tests.
    """

    name = "fake"

    def recognize(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> RecognitionResult:
        return self._parse(filename)

    def identify(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> IdentificationResult:
        parsed = self._parse(filename)
        return IdentificationResult(
            recognized=parsed.recognized,
            entity_type=parsed.entity_type,
            document_type=parsed.document_type,
            confidence=parsed.confidence,
            note=parsed.note,
            provider=self.name,
        )

    def extract(
        self,
        *,
        content: bytes,
        mime_type: str,
        document_type: str,
        entity_type: str | None = None,
        filename: str | None = None,
    ) -> RecognitionResult:
        # Filename parse carries the fields; honour the type decided upstream.
        from dataclasses import replace

        parsed = self._parse(filename)
        return replace(
            parsed,
            document_type=document_type,
            entity_type=entity_type or parsed.entity_type,
        )

    def _parse(self, filename: str | None) -> RecognitionResult:
        if not filename:
            return RecognitionResult(
                recognized=False,
                confidence=0.0,
                provider=self.name,
                note="fake recognizer: no filename to parse",
            )

        stem = filename.rsplit(".", 1)[0]
        parts = stem.split("_")
        first = parts[0] if len(parts) > 0 else None
        last = parts[1] if len(parts) > 1 else None

        doc_type: str | None = None
        for part in parts[2:]:
            key = part.lower()
            if key in _TYPE_WORDS:
                doc_type = _TYPE_WORDS[key]
                break

        start_date, end_date = _parse_dates(stem)
        recognized = bool(doc_type and (first or last))

        return RecognitionResult(
            recognized=recognized,
            entity_type="driver",
            document_type=doc_type,
            first_name=first or None,
            last_name=last or None,
            start_date=start_date,
            end_date=end_date,
            confidence=0.3 if recognized else 0.0,
            provider=self.name,
            note=None if recognized else "fake recognizer: filename not conclusive",
        )
