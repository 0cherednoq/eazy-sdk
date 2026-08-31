"""Small redaction-safe exception surface shared by extraction and storage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from eazy_sdk.redaction import preview, redact_headers, redact_json


@dataclass(frozen=True, slots=True)
class ErrorContext:
    request_id: str | None = None
    operation: str | None = None
    method: str | None = None
    url: str | None = None
    status_code: int | None = None
    rule_name: str | None = None
    headers: Mapping[str, str] | None = None
    body_preview: str | None = None
    json_preview: object | None = None
    pydantic_errors: object | None = None


class EazySDKError(Exception):
    def __init__(self, message: str, *, context: ErrorContext | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def __str__(self) -> str:
        if self.context is None:
            return self.message
        values = [self.message]
        for name in ("operation", "method", "url", "status_code", "rule_name"):
            value = getattr(self.context, name)
            if value is not None:
                values.append(f"{name}={value}")
        return " | ".join(values)

    def to_log_dict(
        self,
        *,
        include_body: bool = False,
        include_headers: bool = True,
        body_limit: int = 2000,
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "error_type": type(self).__name__,
            "message": self.message,
        }
        context = self.context
        if context is None:
            return data
        for name in (
            "request_id",
            "operation",
            "method",
            "url",
            "status_code",
            "rule_name",
            "pydantic_errors",
        ):
            value = getattr(context, name)
            if value is not None:
                data[name] = value
        if include_headers and context.headers is not None:
            data["headers"] = redact_headers(context.headers)
        if context.json_preview is not None:
            data["json_preview"] = redact_json(context.json_preview)
        if include_body and context.body_preview is not None:
            data["body_preview"] = preview(context.body_preview, body_limit)
        return data


class ParameterSerializationError(EazySDKError):
    pass


class HeaderValidationError(EazySDKError):
    pass


class ExtractionError(EazySDKError):
    pass


class MissingExtractedValueError(ExtractionError):
    pass


class ExtractionValidationError(ExtractionError):
    pass


__all__ = [
    "EazySDKError",
    "ErrorContext",
    "ExtractionError",
    "ExtractionValidationError",
    "HeaderValidationError",
    "MissingExtractedValueError",
    "ParameterSerializationError",
]
