"""Document types and expiry-tracking configuration (PRD §4 and §6).

Document types now live in the ``document_type`` table so operators can add new
ones from the UI (see ``app.documents.models.DocumentType``). The lists below are
the *base* catalogue — they seed the table on first run and act as a fallback for
form choices when the table is empty (fresh installs / tests). The status helper
still reads ``UNTRACKED_TYPES`` / threshold tables from here.
"""

from __future__ import annotations

# --- Entity types: who a document type belongs to ----------------------------

ENTITY_DRIVER = "driver"
ENTITY_VEHICLE = "vehicle"
ENTITY_ORGANISATION = "organisation"

# (value, human label) — drives the entity_type SelectField.
ENTITY_TYPES: list[tuple[str, str]] = [
    (ENTITY_DRIVER, "Driver"),
    (ENTITY_VEHICLE, "Vehicle"),
    (ENTITY_ORGANISATION, "Organisation"),
]
ENTITY_TYPE_VALUES = frozenset(v for v, _ in ENTITY_TYPES)

# --- Allowed document types (PRD §4.4 / §6.4) --------------------------------

# (value, human label) — labels are English technical names per the PRD.
DRIVER_DOCUMENT_TYPES: list[tuple[str, str]] = [
    ("passport", "Passport (non-EU)"),
    ("passport_eu", "Passport (EU citizen)"),
    ("visa", "Visa"),
    ("residence", "Residence card (karta pobytu)"),
    ("license", "Driving licence"),
    ("code95", "Driver qualification card (code 95)"),
    ("medical", "Medical exam (badania lekarskie)"),
    ("psychological", "Psychological exam (badania psychologiczne)"),
    ("tacho_card", "Tachograph card (karta kierowcy)"),
    ("adr", "ADR certificate"),
    ("a1", "A1 social security certificate"),
    ("posting", "Posting declaration (oświadczenie o delegowaniu)"),
    ("pesel", "PESEL notification"),
    ("oswiadczenie", "Work permit (oświadczenie)"),
    ("employment", "Employment contract"),
    ("employment_annex", "Employment contract annex"),
]

VEHICLE_DOCUMENT_TYPES: list[tuple[str, str]] = [
    ("tech_passport", "Vehicle registration certificate"),
    ("insurance", "OC insurance"),
    ("inspection", "Technical inspection"),
    ("green_card", "Green Card"),
]

DRIVER_DOCUMENT_VALUES = frozenset(v for v, _ in DRIVER_DOCUMENT_TYPES)
VEHICLE_DOCUMENT_VALUES = frozenset(v for v, _ in VEHICLE_DOCUMENT_TYPES)

# Tachograph card (karta kierowcy): its document number is the driver's
# tachograph card number, kept in step between the driver profile and the
# document (see Driver/DriverDocument tachograph sync rules).
TACHOGRAPH_DOC_TYPE = "tacho_card"

# Base catalogue keyed by entity_type — used to seed the document_type table
# (CLI ``seed-document-types`` and the Alembic migration) and as a form fallback.
BASE_DOCUMENT_TYPES: dict[str, list[tuple[str, str]]] = {
    ENTITY_DRIVER: DRIVER_DOCUMENT_TYPES,
    ENTITY_VEHICLE: VEHICLE_DOCUMENT_TYPES,
    ENTITY_ORGANISATION: [],
}

# --- Expiry alert thresholds (days before end_date), most-distant first ------

GENERIC_THRESHOLDS = (120, 60)        # passport, visa, residence, license, ...
INSURANCE_THRESHOLDS = (60, 30, 15)   # vehicle insurance — tighter scale (PRD §6)

# Types whose expiry is never tracked: identity/permanent cards. Contracts
# (employment / employment_annex) ARE tracked, but only when they carry an
# end_date — the status helper returns "no expiry" for open-ended ones.
UNTRACKED_TYPES = frozenset({"pesel", "tech_passport"})