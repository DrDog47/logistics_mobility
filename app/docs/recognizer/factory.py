"""Recognizer selection and app wiring (PRD §8.7).

The adapter is chosen by config (``DOCUMENT_RECOGNIZER``) so the model/provider
can be swapped without touching the pipeline. Built once at startup and reached
via :func:`get_recognizer`, mirroring the rate registry.
"""

from __future__ import annotations

from flask import Flask, current_app

from app.docs.recognizer.base import (
    DocumentFieldExtractor,
    DocumentIdentifier,
    DocumentRecognizer,
    RecognizerError,
)
from app.docs.recognizer.composite import TwoStageRecognizer
from app.docs.recognizer.fake import FakeRecognizer

_EXT_KEY = "document_recognizer"


def _provider(config, key: str) -> str:
    """Resolve a stage's provider name, falling back to DOCUMENT_RECOGNIZER."""
    name = config.get(key) or config.get("DOCUMENT_RECOGNIZER", "fake")
    return str(name).lower()


def _build_identifier(config) -> DocumentIdentifier:
    name = _provider(config, "DOCUMENT_IDENTIFIER")
    if name == "fake":
        return FakeRecognizer()
    if name == "claude":
        # Imported here so the anthropic dependency is only touched when selected.
        from app.docs.recognizer.claude import ClaudeIdentifier

        return ClaudeIdentifier(
            model=config.get("DOCUMENT_IDENTIFIER_MODEL", "claude-haiku-4-5"),
            api_key=config.get("ANTHROPIC_API_KEY") or None,
        )
    raise RecognizerError(
        f"Unknown identification provider '{name}'. Use 'fake' or 'claude'."
    )


def _build_extractor(config) -> DocumentFieldExtractor:
    name = _provider(config, "DOCUMENT_EXTRACTOR")
    if name == "fake":
        return FakeRecognizer()
    if name == "claude":
        from app.docs.recognizer.claude import ClaudeExtractor

        return ClaudeExtractor(
            model=config.get("DOCUMENT_EXTRACTOR_MODEL", "claude-sonnet-4-6"),
            api_key=config.get("ANTHROPIC_API_KEY") or None,
        )
    raise RecognizerError(
        f"Unknown extraction provider '{name}'. Use 'fake' or 'claude'."
    )


def build_recognizer(config) -> DocumentRecognizer:
    """Assemble the two-stage recognizer from the configured stage adapters."""
    return TwoStageRecognizer(_build_identifier(config), _build_extractor(config))


def init_recognizer(app: Flask) -> None:
    """Build the recognizer at startup and attach it to the app."""
    recognizer = build_recognizer(app.config)
    app.extensions[_EXT_KEY] = recognizer
    app.logger.info("Document recognizer: %s", recognizer.name)


def get_recognizer() -> DocumentRecognizer:
    """Access the recognizer attached to the current app."""
    recognizer = current_app.extensions.get(_EXT_KEY)
    if recognizer is None:
        raise RecognizerError(
            "Recognizer not initialized. Did you call init_recognizer() in the app factory?"
        )
    return recognizer
