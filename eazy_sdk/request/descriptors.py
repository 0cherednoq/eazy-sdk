"""Compact request authoring descriptors for the compiled runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from eazy_sdk.codecs import BodyCodec, ScalarCodec


class BodyProjectionError(RuntimeError):
    """A public-to-wire projection or its target validation failed safely."""


@dataclass(frozen=True, slots=True)
class JsonField:
    """Place one flat input field in an object-shaped JSON request body."""

    name: str | None = None


@dataclass(frozen=True, slots=True)
class Form:
    """Place one flat input field in an URL-encoded form request body."""

    name: str | None = None
    codec: ScalarCodec | None = None


@dataclass(frozen=True, slots=True)
class Part:
    """Place one flat input field in a multipart request body."""

    name: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class JsonBody:
    content_type: str = "application/json"


@dataclass(frozen=True, slots=True, kw_only=True)
class FormBody:
    content_type: str = "application/x-www-form-urlencoded"


@dataclass(frozen=True, slots=True, kw_only=True)
class MultipartBody:
    content_type: str = "multipart/form-data"
    boundary: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BytesBody:
    content_type: str | None = None
    content_encoding: Literal["gzip", "deflate"] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayableStreamBody:
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class BodyProjection[TSource, TWire]:
    """Project caller-visible values into one private semantic wire body."""

    source: type[TSource]
    target: type[TWire]
    using: Callable[[TSource], TWire]
    encoding: JsonBody | FormBody | MultipartBody | BodyCodec
    name: str | None = None

    def __post_init__(self) -> None:
        if not callable(self.using):
            raise TypeError("body projection using must be callable")
        if not isinstance(
            self.encoding,
            JsonBody | FormBody | MultipartBody | BodyCodec,
        ):
            raise TypeError(
                "body projection encoding must be JsonBody, FormBody, MultipartBody, "
                "or BodyCodec"
            )
        if self.name is not None and not self.name:
            raise ValueError("body projection name must not be empty")

    @property
    def fingerprint_name(self) -> str:
        """Return a stable diagnostic identity for the projection callable."""

        if self.name is not None:
            return self.name
        callable_name = getattr(self.using, "__name__", type(self.using).__qualname__)
        return (
            f"{self.source.__module__}.{self.source.__qualname__}"
            f"->{self.target.__module__}.{self.target.__qualname__}"
            f":{callable_name}"
        )


@dataclass(frozen=True, slots=True)
class MultipartPart:
    content: bytes
    filename: str | None = None
    content_type: str | None = None
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class WireOptions:
    query_order: tuple[str, ...] | None = None
    header_order: tuple[str, ...] | None = None
    cookie_order: tuple[str, ...] | None = None
    body_order: tuple[str, ...] | None = None
    exact: bool = False
    protocol: Literal["http/1.1", "http/2", "http/3"] | None = None


type RequestBody = (
    JsonBody | FormBody | MultipartBody | BytesBody | ReplayableStreamBody | BodyCodec
)
