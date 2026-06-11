"""Document recognition (PRD §8.7).

Public API: the ``DocumentRecognizer`` port, its ``RecognitionResult`` output,
and the factory/accessor used to reach the configured adapter.
"""

from app.docs.recognizer.base import (
    DocumentFieldExtractor,
    DocumentIdentifier,
    DocumentRecognizer,
    IdentificationResult,
    RecognitionResult,
    RecognizerError,
)
from app.docs.recognizer.composite import TwoStageRecognizer
from app.docs.recognizer.factory import (
    build_recognizer,
    get_recognizer,
    init_recognizer,
)

__all__ = [
    "DocumentRecognizer",
    "DocumentIdentifier",
    "DocumentFieldExtractor",
    "IdentificationResult",
    "RecognitionResult",
    "RecognizerError",
    "TwoStageRecognizer",
    "build_recognizer",
    "get_recognizer",
    "init_recognizer",
]
