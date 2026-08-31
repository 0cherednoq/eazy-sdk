"""Lossless transport-agnostic response artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .headers import Headers


@dataclass(frozen=True, slots=True)
class RedirectInfo:
    status_code: int
    url: str
    headers: Headers


@dataclass(frozen=True, slots=True, repr=False)
class NormalizedResponse[TRaw = object]:
    status_code: int
    url: str
    method: str | None
    headers: Headers | Mapping[Any, Any] | Iterable[tuple[Any, Any]]
    body: bytes
    redirects: tuple[RedirectInfo, ...] | list[RedirectInfo] = ()
    raw_response: TRaw | None = None
    elapsed: float | None = None
    wire_body: bytes | None = None
    wire_headers: Headers | Mapping[Any, Any] | Iterable[tuple[Any, Any]] | None = None
    effective_content_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.headers, Headers):
            object.__setattr__(self, "headers", Headers(self.headers))
        if not isinstance(self.redirects, tuple):
            object.__setattr__(self, "redirects", tuple(self.redirects))
        if self.wire_headers is not None and not isinstance(self.wire_headers, Headers):
            object.__setattr__(self, "wire_headers", Headers(self.wire_headers))

    def __repr__(self) -> str:
        wire_length = len(self.wire_body) if self.wire_body is not None else len(self.body)
        return (
            "NormalizedResponse("
            f"status_code={self.status_code}, url={self.url!r}, method={self.method!r}, "
            f"body_length={len(self.body)}, wire_body_length={wire_length}, "
            f"content_type={self.content_type!r})"
        )

    def text(self, encoding: str | None = None) -> str:
        selected = encoding or self._charset() or "utf-8"
        try:
            return self.body.decode(selected, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def content_type(self) -> str | None:
        if self.effective_content_type is not None:
            return self.effective_content_type.split(";", 1)[0].strip().lower()
        raw = cast_headers(self.headers).get("content-type")
        return raw.split(";", 1)[0].strip().lower() if raw is not None else None

    def _charset(self) -> str | None:
        raw = cast_headers(self.headers).get("content-type")
        if raw is None:
            return None
        for part in raw.split(";")[1:]:
            name, separator, value = part.strip().partition("=")
            if separator and name.lower() == "charset":
                return value.strip().strip('"') or None
        return None


def cast_headers(value: object) -> Headers:
    if not isinstance(value, Headers):
        raise TypeError("normalized response headers were not normalized")
    return value
