"""Compact request authoring descriptors for the compiled runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from eazy_sdk.codecs import BodyCodec, ScalarCodec

if TYPE_CHECKING:
    from eazy_sdk.dependencies import Injected


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


class Body:
    """The public entry for body encodings: ``Body.json()``, ``Body.multipart(...)``.

    Written at ``encoding=`` on a :class:`BodyProjection`. The root names ``JsonBody`` and
    its siblings are the ``Annotated`` field markers, a different declaration: the classes
    constructed here are the ones :mod:`eazy_sdk.request.markers` publishes.
    """

    __slots__ = ()

    @staticmethod
    def json(*, content_type: str = "application/json") -> JsonBody:
        return JsonBody(content_type=content_type)

    @staticmethod
    def form(*, content_type: str = "application/x-www-form-urlencoded") -> FormBody:
        return FormBody(content_type=content_type)

    @staticmethod
    def multipart(
        *,
        content_type: str = "multipart/form-data",
        boundary: str | None = None,
    ) -> MultipartBody:
        return MultipartBody(content_type=content_type, boundary=boundary)

    @staticmethod
    def raw(
        *,
        content_type: str | None = None,
        content_encoding: Literal["gzip", "deflate"] | None = None,
    ) -> BytesBody:
        return BytesBody(content_type=content_type, content_encoding=content_encoding)

    @staticmethod
    def stream(*, content_type: str | None = None) -> ReplayableStreamBody:
        return ReplayableStreamBody(content_type=content_type)


@dataclass(frozen=True, slots=True)
class BodyProjection[TSource, TWire]:
    """Project caller-visible values into one private semantic wire body.

    ``source`` defaults to the operation class itself: every field without a placement
    marker feeds the projection. ``using`` takes the source value and, when declared with
    two parameters, the resolved ``requires=`` dependencies as a second argument.
    """

    target: type[TWire]
    using: Callable[[TSource], TWire] | Callable[[TSource, Injected], TWire]
    encoding: JsonBody | FormBody | MultipartBody | BodyCodec
    source: type[TSource] | None = None
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
        source = (
            f"{self.source.__module__}.{self.source.__qualname__}"
            if self.source is not None
            else "operation"
        )
        return f"{source}->{self.target.__module__}.{self.target.__qualname__}:{callable_name}"


@dataclass(frozen=True, slots=True)
class MultipartPart:
    content: bytes
    filename: str | None = None
    content_type: str | None = None
    headers: tuple[tuple[str, str], ...] = ()


type RequestBody = (
    JsonBody | FormBody | MultipartBody | BytesBody | ReplayableStreamBody | BodyCodec
)
