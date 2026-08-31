"""Format Pydantic validation errors for ErrorContext."""

from __future__ import annotations


def format_validation_error(exc: Exception) -> object:
    """Return a JSON-serializable representation of a Pydantic error."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return errors(include_url=False)
        except TypeError:
            return errors()
    return str(exc)
