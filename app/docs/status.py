"""Document expiry status (PRD §6).

Pure function — no DB access — so it can be registered as a Jinja global and
called straight from templates to render a status badge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.docs.constants import ENTITY_VEHICLE, UNTRACKED_TYPES

# Status levels (also used as CSS badge modifiers: badge--<level>).
OK = "ok"
SOON = "soon"            # level 1 (yellow): plan ahead
URGENT = "urgent"        # level 2 (red): act now
CRITICAL = "critical"    # insurance level 3 (15 days)
EXPIRED = "expired"
NOT_TRACKED = "not_tracked"
NO_DATE = "no_date"


@dataclass(frozen=True)
class DocStatus:
    level: str
    days_left: int | None
    label: str


def document_status(
    document_type: str,
    end_date: date | None,
    today: date | None = None,
    entity_type: str | None = None,
) -> DocStatus:
    """Classify a document by how close its ``end_date`` is.

    Generic (driver) documents use the 120/60-day scale. **All vehicle
    documents** use the tighter 60/30/15-day scale (PRD vehicle §11.2) — pass
    ``entity_type="vehicle"`` to opt in; driver insurance, if any, also stays on
    the tight scale by type. Untracked types and documents without an
    ``end_date`` get a neutral status.
    """
    today = today or date.today()

    if document_type in UNTRACKED_TYPES:
        return DocStatus(NOT_TRACKED, None, "Not tracked")
    if end_date is None:
        return DocStatus(NO_DATE, None, "No expiry")

    days = (end_date - today).days
    if days < 0:
        return DocStatus(EXPIRED, days, f"Expired {-days}d ago")

    if document_type == "insurance" or entity_type == ENTITY_VEHICLE:
        if days <= 15:
            return DocStatus(CRITICAL, days, f"{days}d left")
        if days <= 30:
            return DocStatus(URGENT, days, f"{days}d left")
        if days <= 60:
            return DocStatus(SOON, days, f"{days}d left")
        return DocStatus(OK, days, f"{days}d left")

    # Generic 120/60 scale.
    if days <= 60:
        return DocStatus(URGENT, days, f"{days}d left")
    if days <= 120:
        return DocStatus(SOON, days, f"{days}d left")
    return DocStatus(OK, days, f"{days}d left")