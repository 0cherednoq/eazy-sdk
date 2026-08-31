"""Helpers that keep secrets out of logs and error context."""

from __future__ import annotations

from collections.abc import Mapping

REDACTED = "***REDACTED***"

REDACT_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-csrf-token",
        "csrf-token",
    }
)
REDACT_JSON_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "api_key",
        "captcha",
        "captcha_token",
        "csrf",
        "csrf_token",
        "client_secret",
        "private_key",
    }
)


def redact_headers(
    headers: Mapping[str, str], *, deny: frozenset[str] = REDACT_HEADERS
) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values masked."""
    return {k: (REDACTED if k.lower() in deny else v) for k, v in headers.items()}


def redact_json(value: object, *, keys: frozenset[str] = REDACT_JSON_KEYS) -> object:
    """Recursively mask sensitive keys in JSON-like data."""
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and k.lower() in keys else redact_json(v, keys=keys))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item, keys=keys) for item in value]
    return value


def preview(text: str | None, limit: int) -> str | None:
    """Truncate ``text`` to ``limit`` characters, appending an ellipsis."""
    if text is None:
        return None
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
