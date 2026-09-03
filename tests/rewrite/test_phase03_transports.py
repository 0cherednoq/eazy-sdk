from __future__ import annotations

import gzip

import httpx
import pytest
import requests
from zapros import URL, Headers, Request

from eazy_sdk.core.http_plan import WireRequirement, WireRequirements
from eazy_sdk.handlers import (
    CapabilityLevel,
    CapabilityMismatchError,
    HandlerProfile,
    validate_profile,
)
from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler
from eazy_sdk.handlers.requests import RequestsHandler
from eazy_sdk.request.prepared import HttpProtocol


def test_sync_zapros_handler_preserves_request_and_repeated_response_headers() -> None:
    captured: list[httpx.Request] = []

    def send(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            201,
            headers=(("X-Item", "a"), ("X-Item", "b")),
            content=b"ok",
            request=request,
        )

    raw = httpx.Client(transport=httpx.MockTransport(send), headers={}, cookies={})
    handler = HttpxHandler(raw, owns_client=False)
    request = Request(
        URL("https://api.test/items?order=1"),
        "POST",
        headers=Headers((("X-Test", "value"),)),
        body=b"payload",
    )

    response = handler.handle(request)

    assert captured[0].url.raw_path == b"/items?order=1"
    assert captured[0].content == b"payload"
    assert response.headers.getall("x-item") == ["a", "b"]
    assert response.read() == b"ok"


def test_httpx_handler_does_not_decode_a_compressed_response_twice() -> None:
    clear = b'{"title":"decoded once"}'

    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": str(len(gzip.compress(clear))),
                "Content-Type": "application/json",
            },
            content=gzip.compress(clear),
            request=request,
        )

    raw = httpx.Client(transport=httpx.MockTransport(send), headers={}, cookies={})
    handler = HttpxHandler(raw, owns_client=True)

    response = handler.handle(Request(URL("https://api.test/items"), "GET"))

    assert response.read() == clear
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(clear))
    handler.close()


@pytest.mark.asyncio
async def test_async_zapros_handler_uses_the_same_boundary() -> None:
    captured: list[httpx.Request] = []

    async def send(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, content=b"ok", request=request)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(send), headers={}, cookies={})
    handler = AsyncHttpxHandler(raw, owns_client=False)
    request = Request(URL("https://api.test/items/1"), "PUT", body=b"payload")

    response = await handler.ahandle(request)

    assert captured[0].method == "PUT"
    assert captured[0].content == b"payload"
    assert response.read() == b"ok"
    await raw.aclose()


def test_handler_profile_is_metadata_and_has_no_send_method() -> None:
    profile = HandlerProfile(
        protocols=frozenset({HttpProtocol.HTTP_1_1}),
        preencoded_body=CapabilityLevel.CAPTURE_VERIFIED,
    )

    assert not hasattr(profile, "send")


def test_handler_profile_rejects_unmet_wire_requirement() -> None:
    profile = HandlerProfile(protocols=frozenset({HttpProtocol.HTTP_1_1}))
    requirement = WireRequirement(
        "preencoded_body",
        CapabilityLevel.CAPTURE_VERIFIED.name,
    )

    with pytest.raises(CapabilityMismatchError, match="preencoded_body"):
        validate_profile(WireRequirements((requirement,)), profile)


def test_requests_handler_fails_closed_for_duplicate_request_headers() -> None:
    session = requests.Session()
    handler = RequestsHandler(session, owns_session=False)
    request = Request(
        URL("https://api.test/items"),
        "GET",
        headers=Headers((("X-Item", "a"), ("X-Item", "b"))),
    )

    with pytest.raises(TypeError, match="does not support duplicate request headers"):
        handler.handle(request)

    session.close()
