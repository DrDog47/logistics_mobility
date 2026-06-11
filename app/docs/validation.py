"""Format rules for recognised/entered document identifiers (PRD §8).

Pure helpers (no DB) so they can validate at every layer: the recognition
confirm form (block save until fixed), the manual Add/Edit forms, and the
persistence layer. ``validate_recognition`` is also registered as a Jinja global
so the inbox confirm form can flag bad fields inline.
"""

from __future__ import annotations

import re
from datetime import date

from app.docs.pipeline import PASSPORT_TYPES

# Latin letters + digits, no spaces / Cyrillic.
_LATIN_ALNUM = re.compile(r"^[A-Za-z0-9]+$")
_PESEL = re.compile(r"^\d{11}$")
_TACHO = re.compile(r"^[A-Za-z0-9]{16}$")
_NATIONALITY = re.compile(r"^[A-Z]{3}$")  # ISO 3166-1 alpha-3

# Document types whose own number (document_id) must be latin alphanumeric.
_LATIN_ID_TYPES = frozenset({"license", "visa", "residence", "adr", "code95"})


def normalize_passport_number(value: str | None) -> str | None:
    """Series + number on one line: drop all whitespace (PRD §8 rule)."""
    if not value:
        return value
    collapsed = re.sub(r"\s+", "", value)
    return collapsed or None


def is_latin_alnum(value: str) -> bool:
    return bool(_LATIN_ALNUM.match(value))


def is_pesel(value: str) -> bool:
    return bool(_PESEL.match(value))


def validate_recognition(result, today: date | None = None) -> dict[str, str]:
    """Return ``{field: message}`` for every identifier that breaks its format
    rule. Empty dict means the record is safe to insert/update.

    ``result`` is any object with the RecognitionResult attributes (the dataclass
    or the manual entry built from the confirm form).
    """
    today = today or date.today()
    errors: dict[str, str] = {}
    dt = getattr(result, "document_type", None)

    ident = getattr(result, "identification_id", None)
    if ident:
        if dt == "pesel":
            if not is_pesel(ident):
                errors["identification_id"] = "PESEL must be exactly 11 digits."
        elif not is_latin_alnum(ident):
            errors["identification_id"] = "Identification number: latin letters and digits only."

    passport = getattr(result, "passport_number", None)
    if passport:
        norm = normalize_passport_number(passport)
        if not norm or not is_latin_alnum(norm):
            errors["passport_number"] = "Passport number: latin letters (series) and digits only."

    doc_id = getattr(result, "document_id", None)
    if doc_id:
        if dt in _LATIN_ID_TYPES and not is_latin_alnum(doc_id):
            errors["document_id"] = "Document number: latin letters and digits only."
        elif dt == "tacho_card" and not _TACHO.match(doc_id):
            errors["document_id"] = "Tachograph card number: 16 latin letters/digits."

    nationality = getattr(result, "nationality", None)
    if nationality and not _NATIONALITY.match(nationality):
        errors["nationality"] = "Nationality: ISO 3166-1 alpha-3 (3 latin capitals)."

    start = getattr(result, "start_date", None)
    end = getattr(result, "end_date", None)
    if start and end and start > end:
        errors["end_date"] = "End date is before the start date."

    birth = getattr(result, "birth_date", None)
    if birth and birth > today:
        errors["birth_date"] = "Birth date is in the future."

    return errors


__all__ = [
    "PASSPORT_TYPES",
    "normalize_passport_number",
    "is_latin_alnum",
    "is_pesel",
    "validate_recognition",
]
