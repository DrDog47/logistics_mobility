"""Document recognition port (PRD §8.7).

Recognition (type, dates, names, identification_id, passport_number) is done by
an LLM, but every caller goes through the ``DocumentRecognizer`` interface so the
model/provider can be swapped via config — including a local model later. The
contract is: given file bytes + MIME type, return a *structured* result, never
free text, so the rest of the pipeline (§8.4–8.5) does not depend on the model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import date


class RecognizerError(RuntimeError):
    """Raised when recognition cannot be performed (config, provider, transport)."""


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """Structured output of recognising a single file.

    All fields are optional except ``recognized`` — a provider that cannot make
    sense of the file returns ``recognized=False`` with a ``note``. Dates are
    ``datetime.date`` (or ``None``); the pipeline maps them to document fields.
    """

    recognized: bool
    entity_type: str | None = None          # "driver" | "vehicle"
    document_type: str | None = None        # catalogue code guess (§8.5.1)
    identification_id: str | None = None     # stable key (§8.4)
    passport_number: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    birth_date: date | None = None           # from passport (§8.4)
    nationality: str | None = None           # ISO 3166-1 alpha-3 (§8.4)
    # Form-only fields (regrouped confirm form): carried through recognition /
    # re-validation so the operator's input survives, but NOT persisted yet.
    pesel: str | None = None
    document_nationality: str | None = None  # nationality/issuing country on the document
    # Vehicle registration certificate (tech_passport) fields — Vehicle PRD §3.1, §8.4.
    registration_country: str | None = None  # ISO 3166-1 alpha-3 (issuing country)
    registration_date: date | None = None    # date the vehicle was registered
    start_date: date | None = None
    end_date: date | None = None
    document_id: str | None = None           # the document's own number
    confidence: float | None = None          # 0..1, provider-reported
    note: str | None = None                  # human-readable hint / why-not
    provider: str | None = None              # adapter/model that produced this

    def to_dict(self) -> dict:
        """JSON-friendly dict (dates as ISO strings)."""
        data = asdict(self)
        for key in ("birth_date", "registration_date", "start_date", "end_date"):
            value = data.get(key)
            if isinstance(value, date):
                data[key] = value.isoformat()
        return data


@dataclass(frozen=True, slots=True)
class IdentificationResult:
    """Type-level outcome of stage one — *what* the document is, not its fields.

    A provider that cannot classify the file returns ``recognized=False`` with a
    ``note``; the two-stage recognizer then skips field extraction (§8.7).
    """

    recognized: bool
    entity_type: str | None = None          # "driver" | "vehicle"
    document_type: str | None = None        # catalogue code guess (§8.5.1)
    confidence: float | None = None          # 0..1, provider-reported
    note: str | None = None                  # human-readable hint / why-not
    provider: str | None = None              # adapter/model that produced this


class DocumentRecognizer(ABC):
    """Port for end-to-end document recognition. Adapters implement :meth:`recognize`."""

    #: Short adapter name (also stamped onto results as ``provider``).
    name: str = "base"

    @abstractmethod
    def recognize(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> RecognitionResult:
        """Recognise one document from its raw bytes.

        Args:
            content: File bytes (PDF or image).
            mime_type: e.g. ``application/pdf``, ``image/jpeg``.
            filename: Original name, if known (a weak hint only).

        Returns:
            A :class:`RecognitionResult`. Implementations must not raise on
            "not our document" — return ``recognized=False`` instead. They may
            raise :class:`RecognizerError` on transport/config failures.
        """
        raise NotImplementedError

    async def arecognize(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> RecognitionResult:
        """Async variant of :meth:`recognize`. Default runs the sync version (no
        real I/O overlap); adapters that do network I/O override it for genuine
        concurrency under ``asyncio.gather``."""
        return self.recognize(content=content, mime_type=mime_type, filename=filename)


class DocumentIdentifier(ABC):
    """Stage-one port: classify the document type (§8.7).

    Deliberately narrow so it can run on a cheap/fast model — or a future local
    model — independently of the heavier field-extraction stage.
    """

    name: str = "base"

    @abstractmethod
    def identify(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> IdentificationResult:
        """Classify one document. Return ``recognized=False`` (not raise) when the
        file is not a recognisable driver/vehicle document; raise
        :class:`RecognizerError` only on transport/config failures."""
        raise NotImplementedError

    async def aidentify(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> IdentificationResult:
        """Async variant of :meth:`identify` (default runs the sync version)."""
        return self.identify(content=content, mime_type=mime_type, filename=filename)


class DocumentFieldExtractor(ABC):
    """Stage-two port: extract fields from a document of *known* type (§8.7).

    Receives the ``document_type`` decided by the identifier so the prompt/model
    can focus on the fields that type carries.
    """

    name: str = "base"

    @abstractmethod
    def extract(
        self,
        *,
        content: bytes,
        mime_type: str,
        document_type: str,
        entity_type: str | None = None,
        filename: str | None = None,
    ) -> RecognitionResult:
        """Extract structured fields for a document already classified as
        ``document_type``. Raise :class:`RecognizerError` on transport/config
        failures."""
        raise NotImplementedError

    async def aextract(
        self,
        *,
        content: bytes,
        mime_type: str,
        document_type: str,
        entity_type: str | None = None,
        filename: str | None = None,
    ) -> RecognitionResult:
        """Async variant of :meth:`extract` (default runs the sync version)."""
        return self.extract(
            content=content,
            mime_type=mime_type,
            document_type=document_type,
            entity_type=entity_type,
            filename=filename,
        )
