"""Zapros handlers backed by HTTPX without HTTP policy ownership."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from importlib.metadata import version
from typing import Any

import httpx
from zapros import AsyncBaseHandler, BaseHandler, Request, Response

from eazy_sdk.handlers.profile import (
    AutomaticHeaderPolicy,
    CapabilityLevel,
    CaptureEvidence,
    HandlerProfile,
    RedirectControl,
)
from eazy_sdk.request.prepared import HttpProtocol

_VERSION = version("httpx")
_FIDELITY = (
    CapabilityLevel.CAPTURE_VERIFIED if _VERSION == "0.28.1" else CapabilityLevel.BEST_EFFORT
)
HTTPX_HANDLER_PROFILE = HandlerProfile(
    protocols=frozenset({HttpProtocol.HTTP_1_1}),
    exact_target=_FIDELITY,
    header_order=_FIDELITY,
    header_casing=_FIDELITY,
    duplicate_headers=_FIDELITY,
    preencoded_body=_FIDELITY,
    manual_cookie_field=_FIDELITY,
    automatic_headers=AutomaticHeaderPolicy.MATERIALIZED,
    redirects=RedirectControl.FORCED_OFF,
    replayable_streams=_FIDELITY,
    evidence=(
        CaptureEvidence("httpx", _VERSION, HttpProtocol.HTTP_1_1, "zapros-handler", "phase18-httpx")
        if _FIDELITY is CapabilityLevel.CAPTURE_VERIFIED
        else None
    ),
)


class HttpxHandler(BaseHandler):
    profile = HTTPX_HANDLER_PROFILE

    def __init__(
        self, client: httpx.Client | None = None, *, owns_client: bool | None = None
    ) -> None:
        self._owned = client is None if owns_client is None else owns_client
        self.client = client or httpx.Client(headers={}, cookies={})

    def handle(self, request: Request) -> Response:
        native = httpx.Request(
            request.method,
            str(request.url),
            headers=_header_pairs(request),
            content=_sync_content(request.body),
        )
        _apply_timeout(native, request)
        result = self.client.send(native, follow_redirects=False)
        return _response(result, request)

    def close(self) -> None:
        if self._owned:
            self.client.close()


class AsyncHttpxHandler(AsyncBaseHandler):
    profile = HTTPX_HANDLER_PROFILE

    def __init__(
        self, client: httpx.AsyncClient | None = None, *, owns_client: bool | None = None
    ) -> None:
        self._owned = client is None if owns_client is None else owns_client
        self.client = client or httpx.AsyncClient(headers={}, cookies={})

    async def ahandle(self, request: Request) -> Response:
        native = httpx.Request(
            request.method,
            str(request.url),
            headers=_header_pairs(request),
            content=await _async_content(request.body),
        )
        _apply_timeout(native, request)
        result = await self.client.send(native, follow_redirects=False)
        return _response(result, request)

    async def aclose(self) -> None:
        if self._owned:
            await self.client.aclose()


def _header_pairs(request: Request) -> list[tuple[str, str]]:
    return [(name, value) for name in request.headers for value in request.headers.getall(name)]


def _sync_content(body: object) -> Any:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, AsyncIterator):
        raise TypeError("sync HTTPX handler cannot consume an async request body")
    if isinstance(body, Iterator):
        return body
    raise TypeError(f"unsupported Zapros request body: {type(body).__name__}")


async def _async_content(body: object) -> Any:
    if isinstance(body, AsyncIterator):
        return body
    if isinstance(body, Iterator):

        async def bridge() -> AsyncIterator[bytes]:
            for chunk in body:
                yield chunk

        return bridge()
    return _sync_content(body)


def _apply_timeout(native: httpx.Request, request: Request) -> None:
    timeouts = request.context.get("timeouts")
    total = timeouts.get("total") if timeouts is not None else None
    if total is None:
        return
    timeout = httpx.Timeout(total)
    native.extensions["timeout"] = {
        "connect": timeout.connect,
        "read": timeout.read,
        "write": timeout.write,
        "pool": timeout.pool,
    }


def _response(native: httpx.Response, request: Request) -> Response:
    # HTTPX exposes decoded ``content`` while retaining the server's encoding and
    # compressed length headers. Zapros would otherwise try to decode those bytes
    # a second time.
    headers = [
        (name, value)
        for name, value in native.headers.multi_items()
        if name.lower() not in {"content-encoding", "content-length"}
    ]
    return Response(
        native.status_code,
        headers,
        content=native.content,
        request=request,
    )


__all__ = ["HTTPX_HANDLER_PROFILE", "AsyncHttpxHandler", "HttpxHandler"]
