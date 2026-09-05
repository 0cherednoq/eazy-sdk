"""Synchronous Zapros handler backed by requests.Session."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from importlib.metadata import version

import requests
from zapros import BaseHandler, Request, Response

from eazy_sdk.handlers.profile import (
    AutomaticHeaderPolicy,
    CapabilityLevel,
    CaptureEvidence,
    HandlerProfile,
    RedirectControl,
)
from eazy_sdk.request.prepared import HttpProtocol

_VERSION = version("requests")
_BODY_FIDELITY = (
    CapabilityLevel.CAPTURE_VERIFIED if _VERSION == "2.34.2" else CapabilityLevel.BEST_EFFORT
)
REQUESTS_HANDLER_PROFILE = HandlerProfile(
    protocols=frozenset({HttpProtocol.HTTP_1_1}),
    exact_target=CapabilityLevel.BEST_EFFORT,
    header_order=CapabilityLevel.BEST_EFFORT,
    header_casing=CapabilityLevel.BEST_EFFORT,
    duplicate_headers=CapabilityLevel.UNSUPPORTED,
    preencoded_body=_BODY_FIDELITY,
    manual_cookie_field=_BODY_FIDELITY,
    automatic_headers=AutomaticHeaderPolicy.TRANSPORT_CONTROLLED,
    redirects=RedirectControl.FORCED_OFF,
    replayable_streams=CapabilityLevel.UNSUPPORTED,
    evidence=(
        CaptureEvidence(
            "requests",
            _VERSION,
            HttpProtocol.HTTP_1_1,
            "zapros-handler",
            "phase18-requests",
        )
        if _BODY_FIDELITY is CapabilityLevel.CAPTURE_VERIFIED
        else None
    ),
)


class RequestsHandler(BaseHandler):
    profile = REQUESTS_HANDLER_PROFILE

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        owns_session: bool | None = None,
        proxy: str | None = None,
    ) -> None:
        self._owned = session is None if owns_session is None else owns_session
        self.session = session or requests.Session()
        if session is None:
            self.session.headers.clear()
            self.session.cookies.clear()
            if proxy is not None:
                self.session.proxies = {"http": proxy, "https": proxy}
        self.profile = (
            REQUESTS_HANDLER_PROFILE
            if proxy is None
            else replace(REQUESTS_HANDLER_PROFILE, proxy=proxy)
        )

    def handle(self, request: Request) -> Response:
        body = _content(request.body)
        native_request = requests.Request(
            request.method,
            str(request.url),
            headers=_headers(request),
            data=body,
        ).prepare()
        timeouts = request.context.get("timeouts")
        timeout = timeouts.get("total") if timeouts is not None else None
        native = self.session.send(
            native_request,
            allow_redirects=False,
            timeout=timeout,
        )
        return Response(
            native.status_code,
            list(native.headers.items()),
            content=native.content,
            request=request,
        )

    def close(self) -> None:
        if self._owned:
            self.session.close()


def _content(body: object) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, AsyncIterator):
        raise TypeError("requests handler cannot consume an async request body")
    if isinstance(body, Iterator):
        raise TypeError("requests handler does not support streaming request bodies")
    raise TypeError(f"unsupported Zapros request body: {type(body).__name__}")


def _headers(request: Request) -> dict[str, str]:
    pairs = [(name, value) for name in request.headers for value in request.headers.getall(name)]
    if len({name.casefold() for name, _ in pairs}) != len(pairs):
        raise TypeError("requests handler does not support duplicate request headers")
    return dict(pairs)


__all__ = ["REQUESTS_HANDLER_PROFILE", "RequestsHandler"]
