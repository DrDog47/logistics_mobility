"""Cloud LLM adapter — Claude API (PRD §8.7, default cloud provider).

Implements the two recognition stages as separate adapters so each can run on its
own model (a cheap one for identification, a stronger one for field extraction)
and be swapped for a local model independently:

* :class:`ClaudeIdentifier` — stage one, classifies the document type.
* :class:`ClaudeExtractor`   — stage two, extracts fields for a known type.

Both share :class:`_ClaudeClient` (SDK client, file blocks, JSON call). The
``anthropic`` SDK is imported lazily — only needed when an adapter is selected.

Config: ``DOCUMENT_IDENTIFIER=claude`` / ``DOCUMENT_EXTRACTOR=claude`` with
``DOCUMENT_IDENTIFIER_MODEL`` / ``DOCUMENT_EXTRACTOR_MODEL`` and
``ANTHROPIC_API_KEY`` (read by the SDK from the env).
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date

from app.docs.constants import BASE_DOCUMENT_TYPES
from app.docs.recognizer.base import (
    DocumentFieldExtractor,
    DocumentIdentifier,
    IdentificationResult,
    RecognitionResult,
    RecognizerError,
)

# --- Schemas (structured-output shape; also embedded into the system prompt) --

# Stage one: classification only.
_IDENT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recognized": {"type": "boolean"},
        "entity_type": {"type": "string"},
        "document_type": {"type": "string"},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
    "required": ["recognized"],
}

# Stage two: the full field set. Optional fields are omitted (or "") when unread.
_EXTRACT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recognized": {"type": "boolean"},
        "entity_type": {"type": "string"},
        "document_type": {"type": "string"},
        "identification_id": {"type": "string"},
        "passport_number": {"type": "string"},
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "birth_date": {"type": "string"},
        "nationality": {"type": "string"},
        "registration_country": {"type": "string"},
        "registration_date": {"type": "string"},
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
        "document_id": {"type": "string"},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
    "required": ["recognized"],
}


def _system(schema: dict) -> str:
    return (
        "Respond ONLY with valid JSON matching this schema exactly. "
        "No markdown fences, no explanation, no extra keys.\n\n"
        f"Schema:\n{json.dumps(schema, indent=2)}"
    )


def _catalogue() -> str:
    """Bulleted ``code — label`` list of valid document types, kept in sync with
    the catalogue (§8.5.1) so the prompt never drifts from the form choices."""
    lines: list[str] = []
    for entity_type, types in BASE_DOCUMENT_TYPES.items():
        for code, label in types:
            lines.append(f"- {code} ({entity_type}): {label}")
    return "\n".join(lines)


_IDENT_PROMPT = (
    "You are classifying a scanned document for a Polish trucking company. "
    "Decide what kind of document it is — do NOT extract any other fields yet.\n"
    "- entity_type is 'driver' for driver documents, 'vehicle' for vehicle documents.\n"
    "- document_type must be one of these catalogue codes:\n"
    f"{_catalogue()}\n"
    "- confidence is 0..1.\n"
    "If the file is not a recognisable driver/vehicle document, set "
    "recognized=false and explain briefly in note."
)


def _extract_prompt(document_type: str) -> str:
    return (
        "You are extracting structured data from a scanned driver document for a "
        f"Polish trucking company. The document has already been classified as "
        f"'{document_type}'. Extract the key fields. Rules:\n"
        "- identification_id is the stable personal identification number "
        "(labelled 'identification number' on most passports), NOT the passport "
        "booklet number. It contains LATIN letters and digits only — no spaces, "
        "no Cyrillic.\n"
        "- passport_number is the booklet number: a series (latin letters) "
        "followed by a number (digits). Return it as ONE token with NO spaces, "
        "e.g. 'AB1234567'. Latin letters and digits only.\n"
        "- For a PESEL document, identification_id is the 11-digit PESEL number "
        "(digits only).\n"
        "- For a driving licence, document_id contains latin letters and digits "
        "only (no spaces).\n"
        "- For a tachograph card, document_id is 16 latin letters/digits.\n"
        "- For visa / residence / adr / code95, document_id is latin letters and "
        "digits only (no spaces).\n"
        "- start_date / end_date are the document validity dates in ISO format "
        "YYYY-MM-DD; use null if absent.\n"
        "- first_name / last_name as Latin spelling from the document.\n"
        "- birth_date in ISO format YYYY-MM-DD; null if absent.\n"
        "- nationality as ISO 3166-1 alpha-3, three latin capitals (e.g. BLR, "
        "POL); null if unclear.\n"
        "- For a vehicle registration certificate (tech_passport / 'Dowód "
        "rejestracyjny'): registration_country is the issuing country as ISO "
        "3166-1 alpha-3 (e.g. POL), and registration_date is the date the vehicle "
        "was registered in ISO format YYYY-MM-DD; null if absent.\n"
        "- document_id is the document's own number, if distinct from the above.\n"
        "- confidence is 0..1.\n"
        "If the file turns out not to match this type, set recognized=false and "
        "explain briefly in note."
    )


def _parse_date(value: object) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _clean(value: object) -> str | None:
    """Empty/whitespace strings → None (the model returns '' for unknown fields)."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply.

    Without structured outputs the response isn't guaranteed to be bare JSON:
    the model may wrap it in ```json fences or emit a short preamble. Try a
    direct parse first, then fall back to the first {...} block.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise json.JSONDecodeError("no JSON object found", text, 0)


class _ClaudeClient:
    """Shared SDK plumbing for the Claude recognition adapters."""

    name = "claude"

    def __init__(self, *, model: str, api_key: str | None = None):
        self._model = model
        self._api_key = api_key or None
        self._client = None  # lazy

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RecognizerError(
                "The 'anthropic' package is required for the Claude recognizer. "
                "Install it (pip install anthropic) or use the 'fake' provider."
            ) from exc
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY from the env.
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _file_block(self, *, content: bytes, mime_type: str) -> dict:
        b64 = base64.standard_b64encode(content).decode("ascii")
        if mime_type == "application/pdf":
            return {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            }
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": b64},
        }

    def _complete_json(
        self,
        *,
        system: str,
        prompt: str,
        content: bytes,
        mime_type: str,
        max_tokens: int,
    ) -> dict:
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            self._file_block(content=content, mime_type=mime_type),
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
        except RecognizerError:
            raise
        except Exception as exc:  # transport / API errors
            raise RecognizerError(f"Claude recognizer failed: {exc}") from exc

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise RecognizerError("Claude recognizer returned no text content.")
        try:
            return _extract_json(text)
        except json.JSONDecodeError as exc:
            raise RecognizerError(f"Claude recognizer returned invalid JSON: {exc}") from exc

    async def _acomplete_json(
        self,
        *,
        system: str,
        prompt: str,
        content: bytes,
        mime_type: str,
        max_tokens: int,
    ) -> dict:
        """Async twin of :meth:`_complete_json`. A fresh ``AsyncAnthropic`` is
        created per call so it binds to the running event loop (the inbox batch
        runs each request on its own loop via ``asyncio.run``)."""
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RecognizerError(
                "The 'anthropic' package is required for the Claude recognizer."
            ) from exc
        messages = [
            {
                "role": "user",
                "content": [
                    self._file_block(content=content, mime_type=mime_type),
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        try:
            async with anthropic.AsyncAnthropic(api_key=self._api_key) as client:
                response = await client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                )
        except RecognizerError:
            raise
        except Exception as exc:  # transport / API errors
            raise RecognizerError(f"Claude recognizer failed: {exc}") from exc

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise RecognizerError("Claude recognizer returned no text content.")
        try:
            return _extract_json(text)
        except json.JSONDecodeError as exc:
            raise RecognizerError(f"Claude recognizer returned invalid JSON: {exc}") from exc

    @property
    def provider(self) -> str:
        return f"{self.name}:{self._model}"


class ClaudeIdentifier(_ClaudeClient, DocumentIdentifier):
    """Stage one — classify the document type via Claude."""

    def __init__(self, *, model: str = "claude-haiku-4-5", api_key: str | None = None):
        super().__init__(model=model, api_key=api_key)

    def _ident_args(self, *, content, mime_type, filename):
        hint = f"\nOriginal filename (weak hint): {filename}" if filename else ""
        return dict(
            system=_system(_IDENT_SCHEMA),
            prompt=_IDENT_PROMPT + hint,
            content=content,
            mime_type=mime_type,
            max_tokens=512,
        )

    def _to_result(self, data: dict) -> IdentificationResult:
        return IdentificationResult(
            recognized=bool(data.get("recognized")),
            entity_type=_clean(data.get("entity_type")),
            document_type=_clean(data.get("document_type")),
            confidence=data.get("confidence"),
            note=_clean(data.get("note")),
            provider=self.provider,
        )

    def identify(self, *, content, mime_type, filename=None) -> IdentificationResult:
        data = self._complete_json(**self._ident_args(content=content, mime_type=mime_type, filename=filename))
        return self._to_result(data)

    async def aidentify(self, *, content, mime_type, filename=None) -> IdentificationResult:
        data = await self._acomplete_json(
            **self._ident_args(content=content, mime_type=mime_type, filename=filename)
        )
        return self._to_result(data)


class ClaudeExtractor(_ClaudeClient, DocumentFieldExtractor):
    """Stage two — extract fields for a known document type via Claude."""

    def __init__(self, *, model: str = "claude-sonnet-4-6", api_key: str | None = None):
        super().__init__(model=model, api_key=api_key)

    def _extract_args(self, *, content, mime_type, document_type, filename):
        hint = f"\nOriginal filename (weak hint): {filename}" if filename else ""
        return dict(
            system=_system(_EXTRACT_SCHEMA),
            prompt=_extract_prompt(document_type) + hint,
            content=content,
            mime_type=mime_type,
            max_tokens=2048,
        )

    def _to_result(self, data: dict, document_type: str, entity_type: str | None) -> RecognitionResult:
        return RecognitionResult(
            recognized=bool(data.get("recognized")),
            entity_type=_clean(data.get("entity_type")) or entity_type,
            document_type=_clean(data.get("document_type")) or document_type,
            identification_id=_clean(data.get("identification_id")),
            passport_number=_clean(data.get("passport_number")),
            first_name=_clean(data.get("first_name")),
            last_name=_clean(data.get("last_name")),
            birth_date=_parse_date(data.get("birth_date")),
            nationality=_clean(data.get("nationality")),
            registration_country=_clean(data.get("registration_country")),
            registration_date=_parse_date(data.get("registration_date")),
            start_date=_parse_date(data.get("start_date")),
            end_date=_parse_date(data.get("end_date")),
            document_id=_clean(data.get("document_id")),
            confidence=data.get("confidence"),
            note=_clean(data.get("note")),
            provider=self.provider,
        )

    def extract(self, *, content, mime_type, document_type, entity_type=None, filename=None) -> RecognitionResult:
        data = self._complete_json(
            **self._extract_args(content=content, mime_type=mime_type, document_type=document_type, filename=filename)
        )
        return self._to_result(data, document_type, entity_type)

    async def aextract(self, *, content, mime_type, document_type, entity_type=None, filename=None) -> RecognitionResult:
        data = await self._acomplete_json(
            **self._extract_args(content=content, mime_type=mime_type, document_type=document_type, filename=filename)
        )
        return self._to_result(data, document_type, entity_type)
