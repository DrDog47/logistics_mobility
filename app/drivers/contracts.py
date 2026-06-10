"""Driver contracts as documents.

A contract is no longer its own table — it is a :class:`DriverDocument` of the
``employment`` type whose terms (sub-type, gross salary, monthly hours norm) live
in the document's ``extra`` JSONB. Number → ``document_id``, validity →
``start_date`` / ``end_date``.

Templates read ``doc.extra`` directly; Python callers (payroll) use the
structured :class:`ContractTerms` view returned by :func:`contract_terms`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.drivers.models import ContractType

if TYPE_CHECKING:
    from app.documents.models import DriverDocument
    from app.drivers.models import Driver

# The document_type code that represents an employment contract.
EMPLOYMENT_DOC_TYPE = "employment"


@dataclass(frozen=True)
class ContractTerms:
    """Structured view of a contract stored on an ``employment`` document."""

    document: "DriverDocument"
    contract_type: ContractType | None
    start_date: date | None
    end_date: date | None
    base_salary_pln: Decimal
    hours_norm: int
    number: str | None


def contract_terms(doc: "DriverDocument") -> ContractTerms:
    """Parse an ``employment`` document's columns + ``extra`` into terms."""
    extra = doc.extra or {}
    raw_type = extra.get("contract_type")
    ctype = ContractType(raw_type) if raw_type in ContractType._value2member_map_ else None
    return ContractTerms(
        document=doc,
        contract_type=ctype,
        start_date=doc.start_date,
        end_date=doc.end_date,
        base_salary_pln=Decimal(str(extra.get("base_salary_pln") or "0")),
        hours_norm=int(extra.get("hours_norm") or 168),
        number=doc.document_id,
    )


def contract_documents(driver: "Driver") -> list:
    """Active (non-deleted, non-archived) employment documents, newest first."""
    docs = [
        d
        for d in driver.documents
        if not d.is_deleted
        and d.archived_at is None
        and d.document_type == EMPLOYMENT_DOC_TYPE
    ]
    return sorted(docs, key=lambda d: d.start_date or date.min, reverse=True)


def current_contract_doc(driver: "Driver", on_date: date | None = None):
    """The employment document whose validity spans ``on_date`` (today by default)."""
    on_date = on_date or date.today()
    for d in contract_documents(driver):
        if (
            d.start_date
            and d.start_date <= on_date
            and (d.end_date is None or d.end_date >= on_date)
        ):
            return d
    return None
