"""Safe public views for operation preparation without a network send."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from eazy_sdk.core.errors import EazySdkError
from eazy_sdk.policies import CallOptions
from eazy_sdk.request.prepared import BufferedBody, PreparedRequest

_REDACTED = "<redacted>"


@dataclass(frozen=True, slots=True)
class PrepareOptions:
    """Control whether preparation may resolve managed request values."""

    resolve_managed: bool = False
    call_options: CallOptions | None = None


class PreparationIncompleteError(EazySdkError):
    """Pure preparation needs one or more managed values."""

    def __init__(self, requirements: tuple[str, ...]) -> None:
        self.requirements = requirements
        super().__init__("managed values are required: " + ", ".join(requirements))


@dataclass(frozen=True, slots=True)
class PreparedValue:
    name: str
    value: str
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class PreparedCall:
    """Immutable redacted view of the exact request stopped before emission."""

    method: str
    url: str
    target: str
    query: tuple[PreparedValue, ...]
    headers: tuple[PreparedValue, ...]
    cookies: tuple[PreparedValue, ...]
    body: object | None
    encoded_body: bytes | None
    protocol: str

    @classmethod
    def _from_request(cls, request: PreparedRequest) -> PreparedCall:
        view = request.view
        if view is None:
            raise TypeError("prepared request has no safe inspection view")
        query = tuple(
            PreparedValue(
                pair.name.decode("utf-8", errors="replace"),
                _REDACTED
                if pair.slot is not None and pair.slot.secret
                else pair.value.decode("utf-8", errors="replace"),
                pair.slot is not None and pair.slot.secret,
            )
            for pair in view.query_pairs
        )
        headers = tuple(
            PreparedValue(
                field.name.decode("ascii", errors="replace"),
                _REDACTED
                if field.sensitive
                else field.value.decode("utf-8", errors="replace"),
                field.sensitive,
            )
            for field in view.headers
        )
        cookies = tuple(
            PreparedValue(
                cookie.name.decode("ascii", errors="replace"),
                _REDACTED
                if cookie.sensitive
                else cookie.value.decode("utf-8", errors="replace"),
                cookie.sensitive,
            )
            for cookie in view.cookies
        )
        target = _safe_target(view.path, query)
        body = _REDACTED if view.body.sensitive else _semantic_body(view.body)
        encoded = None
        if not view.body.sensitive and isinstance(request.body, BufferedBody):
            encoded = request.body.content
        prefix = (
            request.scheme.decode("ascii")
            + "://"
            + request.authority.decode("ascii")
        )
        return cls(
            method=request.method.decode("ascii"),
            url=prefix + target,
            target=target,
            query=query,
            headers=headers,
            cookies=cookies,
            body=body,
            encoded_body=encoded,
            protocol=request.protocol.value,
        )


def _safe_target(path: bytes, query: tuple[PreparedValue, ...]) -> str:
    result = path.decode("ascii", errors="replace")
    if query:
        result += "?" + "&".join(f"{item.name}={item.value}" for item in query)
    return result


def _semantic_body(body: object) -> object | None:
    json_view = getattr(body, "json_view", None)
    if json_view is not None:
        return cast(object, json_view)
    form_fields = getattr(body, "form_fields", None)
    if form_fields is not None:
        return tuple(
            (
                field.name.decode("utf-8", errors="replace"),
                field.value.decode("utf-8", errors="replace"),
            )
            for field in form_fields
        )
    content = cast(object, getattr(body, "content", b""))
    if not content:
        return None
    return content


__all__ = [
    "PreparationIncompleteError",
    "PrepareOptions",
    "PreparedCall",
    "PreparedValue",
]
