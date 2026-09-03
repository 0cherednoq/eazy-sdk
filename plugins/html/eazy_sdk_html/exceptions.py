"""Extraction errors (subclasses of the core ``EazySdkError``)."""

from __future__ import annotations

from eazy_sdk.exceptions import EazySdkContextError


class ExtractionError(EazySdkContextError):
    pass


class MissingExtractedValueError(ExtractionError):
    pass


class ExtractionValidationError(ExtractionError):
    pass


__all__ = ["ExtractionError", "ExtractionValidationError", "MissingExtractedValueError"]
