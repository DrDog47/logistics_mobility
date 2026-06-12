"""Two-stage recognizer: identify the type, then extract the fields (PRD §8.7).

Composes a :class:`DocumentIdentifier` (stage one — cheap/fast or local model) and
a :class:`DocumentFieldExtractor` (stage two — stronger model) behind the existing
:class:`DocumentRecognizer` port, so the inbox pipeline keeps consuming a single
``recognize() -> RecognitionResult`` contract while the two stages stay swappable
and independently configurable.
"""

from __future__ import annotations

from dataclasses import replace

from app.docs.recognizer.base import (
    DocumentFieldExtractor,
    DocumentIdentifier,
    DocumentRecognizer,
    RecognitionResult,
)


class TwoStageRecognizer(DocumentRecognizer):
    """Identify → extract. Stage two is skipped when stage one can't classify."""

    def __init__(
        self,
        identifier: DocumentIdentifier,
        extractor: DocumentFieldExtractor,
    ):
        self._identifier = identifier
        self._extractor = extractor

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{self._identifier.name}+{self._extractor.name}"

    def recognize(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> RecognitionResult:
        ident = self._identifier.identify(
            content=content, mime_type=mime_type, filename=filename
        )

        # Stage one drew a blank — don't spend the heavier extraction call.
        if not ident.recognized or not ident.document_type:
            return RecognitionResult(
                recognized=False,
                entity_type=ident.entity_type,
                document_type=ident.document_type,
                confidence=ident.confidence,
                note=ident.note or "identification stage could not classify the document",
                provider=ident.provider,
            )

        result = self._extractor.extract(
            content=content,
            mime_type=mime_type,
            document_type=ident.document_type,
            entity_type=ident.entity_type,
            filename=filename,
        )

        # The identified type is authoritative; record both providers.
        return replace(
            result,
            entity_type=result.entity_type or ident.entity_type,
            document_type=result.document_type or ident.document_type,
            provider=f"{ident.provider}+{result.provider}",
        )

    async def arecognize(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str | None = None,
    ) -> RecognitionResult:
        """Async identify → extract, so a batch can run under ``asyncio.gather``
        with each stage awaiting its (async) adapter."""
        ident = await self._identifier.aidentify(
            content=content, mime_type=mime_type, filename=filename
        )
        if not ident.recognized or not ident.document_type:
            return RecognitionResult(
                recognized=False,
                entity_type=ident.entity_type,
                document_type=ident.document_type,
                confidence=ident.confidence,
                note=ident.note or "identification stage could not classify the document",
                provider=ident.provider,
            )
        result = await self._extractor.aextract(
            content=content,
            mime_type=mime_type,
            document_type=ident.document_type,
            entity_type=ident.entity_type,
            filename=filename,
        )
        return replace(
            result,
            entity_type=result.entity_type or ident.entity_type,
            document_type=result.document_type or ident.document_type,
            provider=f"{ident.provider}+{result.provider}",
        )
