"""Zapros handlers backed by curl_cffi.

The handlers deliberately keep Zapros as the HTTP-semantics boundary: they receive an
already-built :class:`zapros.Request` and only translate it to curl_cffi's native call.
Redirects, cookies, retries, and curl-generated default headers are disabled.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from importlib.metadata import version
from typing import Any, cast

from curl_cffi import requests as curl_requests
from curl_cffi.requests.impersonate import BrowserTypeLiteral
from zapros import AsyncBaseHandler, BaseHandler, Headers, Request, Response

from eazy_sdk.handlers.profile import (
    AutomaticHeaderPolicy,
    CapabilityLevel,
    CaptureEvidence,
    HandlerProfile,
    RedirectControl,
)
from eazy_sdk.request.prepared import HttpProtocol

_CURL_IMPLICIT_HEADERS = ("Accept", "Accept-Encoding", "Content-Type", "User-Agent")
_VERSION = version("curl_cffi")
_FIDELITY = (
    CapabilityLevel.CAPTURE_VERIFIED if _VERSION == "0.15.0" else CapabilityLevel.BEST_EFFORT
)
CURL_CFFI_HANDLER_PROFILE = HandlerProfile(
    protocols=frozenset({HttpProtocol.HTTP_1_1}),
    exact_target=CapabilityLevel.BEST_EFFORT,
    header_order=_FIDELITY,
    header_casing=_FIDELITY,
    duplicate_headers=_FIDELITY,
    preencoded_body=_FIDELITY,
    manual_cookie_field=_FIDELITY,
    automatic_headers=AutomaticHeaderPolicy.MATERIALIZED,
    redirects=RedirectControl.FORCED_OFF,
    replayable_streams=CapabilityLevel.UNSUPPORTED,
    evidence=(
        CaptureEvidence(
            "curl_cffi",
            _VERSION,
            HttpProtocol.HTTP_1_1,
            "zapros-handler",
            "phase18-curl-cffi",
        )
        if _FIDELITY is CapabilityLevel.CAPTURE_VERIFIED
        else None
    ),
)


class CurlCffiZaprosHandler(BaseHandler):
    """Synchronous Zapros handler using a curl_cffi session."""

    profile = CURL_CFFI_HANDLER_PROFILE

    def __init__(
        self,
        session: curl_requests.Session | None = None,
        *,
        impersonate: BrowserTypeLiteral | None = None,
        owns_session: bool | None = None,
    ) -> None:
        self._owned = session is None if owns_session is None else owns_session
        self._session = session or curl_requests.Session()
        self._impersonate = impersonate

    def handle(self, request: Request) -> Response:
        body, materialized_stream = _read_sync_body(request.body)
        native = self._session.request(
            method=cast(Any, request.method),
            url=str(request.url),
            headers=_curl_headers(request.headers, body, materialized_stream),
            data=body,
            allow_redirects=False,
            default_headers=False,
            accept_encoding=None,
            discard_cookies=True,
            quote=False,
            impersonate=self._impersonate,
            timeout=_total_timeout(request),
        )
        return _response(native, request)

    def close(self) -> None:
        if self._owned:
            self._session.close()


class AsyncCurlCffiZaprosHandler(AsyncBaseHandler):
    """Asynchronous Zapros handler using a curl_cffi async session."""

    profile = CURL_CFFI_HANDLER_PROFILE

    def __init__(
        self,
        session: curl_requests.AsyncSession | None = None,
        *,
        impersonate: BrowserTypeLiteral | None = None,
        owns_session: bool | None = None,
    ) -> None:
        self._owned = session is None if owns_session is None else owns_session
        self._session = session or curl_requests.AsyncSession()
        self._impersonate = impersonate

    async def ahandle(self, request: Request) -> Response:
        body, materialized_stream = await _read_async_body(request.body)
        native = await self._session.request(
            method=cast(Any, request.method),
            url=str(request.url),
            headers=_curl_headers(request.headers, body, materialized_stream),
            data=body,
            allow_redirects=False,
            default_headers=False,
            accept_encoding=None,
            discard_cookies=True,
            quote=False,
            impersonate=self._impersonate,
            timeout=_total_timeout(request),
        )
        return _response(native, request)

    async def aclose(self) -> None:
        if self._owned:
            await self._session.close()


def _header_pairs(headers: Headers) -> list[tuple[str, str]]:
    # Zapros 0.16.0 exposes per-name multi-values through getall(), but does not expose
    # the underlying globally interleaved raw item list. Preserve unique-name order and
    # value order within each name, which is the strongest public contract available.
    return [(name, value) for name in headers for value in headers.getall(name)]


def _curl_headers(
    headers: Headers, body: bytes | None, materialized_stream: bool
) -> curl_requests.Headers:
    pairs = _header_pairs(headers)
    if materialized_stream:
        pairs = [
            (name, value)
            for name, value in pairs
            if name.casefold() not in {"content-length", "transfer-encoding"}
        ]
        pairs.append(("Content-Length", str(len(body or b""))))

    output = curl_requests.Headers(pairs)
    present = {name.casefold() for name, _ in pairs}
    for name in _CURL_IMPLICIT_HEADERS:
        if name.casefold() not in present:
            # curl_cffi represents an explicitly disabled header with a None value and
            # translates that value to libcurl's ``Name:`` suppression form. Passing the
            # literal string ``Name:`` would instead be parsed as an empty value and sent.
            output[name] = None
    return output


def _read_sync_body(body: object) -> tuple[bytes | None, bool]:
    if body is None:
        return None, False
    if isinstance(body, bytes):
        return body, False
    if isinstance(body, AsyncIterator):
        raise TypeError("async Zapros request body cannot be sent by a sync curl_cffi handler")
    if isinstance(body, Iterator):
        raise TypeError("curl_cffi handler does not support streaming request bodies")
    raise TypeError(f"unsupported Zapros request body: {type(body).__name__}")


async def _read_async_body(body: object) -> tuple[bytes | None, bool]:
    if body is None:
        return None, False
    if isinstance(body, bytes):
        return body, False
    if isinstance(body, AsyncIterator):
        raise TypeError("curl_cffi handler does not support async streaming request bodies")
    if isinstance(body, Iterator):
        raise TypeError("curl_cffi handler does not support streaming request bodies")
    raise TypeError(f"unsupported Zapros request body: {type(body).__name__}")


def _total_timeout(request: Request) -> float | None:
    timeouts = request.context.get("timeouts")
    return None if timeouts is None else timeouts.get("total")


def _response(native: Any, request: Request) -> Response:
    raw_headers = getattr(native, "headers", {})
    raw_pairs = (
        list(raw_headers.multi_items())
        if hasattr(raw_headers, "multi_items")
        else list(raw_headers.items())
    )
    pairs: list[tuple[str, str]] = [
        (str(name), str(value)) for name, value in raw_pairs if value is not None
    ]
    return Response(
        int(native.status_code),
        pairs,
        content=bytes(native.content),
        request=request,
    )


__all__ = [
    "CURL_CFFI_HANDLER_PROFILE",
    "AsyncCurlCffiZaprosHandler",
    "CurlCffiZaprosHandler",
]
