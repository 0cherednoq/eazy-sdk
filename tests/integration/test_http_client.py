from __future__ import annotations

from io import BytesIO
from typing import Annotated, cast

import httpx
import pytest

from eazy_sdk import AsyncApi, SyncApi, TransportFailure, api
from eazy_sdk.clients import CallOptions
from eazy_sdk.request import ReplayableStreamBody
from eazy_sdk.request.prepared import ReplayableBodyStream
from eazy_sdk.response import Json, Responses, Success
from eazy_sdk.response.normalized import cast_headers
from tests._support.http_server import LocalHttpServer
from tests._support.zapros_clients import client_from_httpx

pytestmark = pytest.mark.integration


class StreamApi(SyncApi):
    @api.post(
        "/echo",
        operation_id="stream.sync",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def upload(
        self,
        *,
        body: Annotated[ReplayableBodyStream, ReplayableStreamBody()],
    ) -> dict[str, object]:
        raise NotImplementedError


class AsyncStreamApi(AsyncApi):
    @api.post(
        "/echo",
        operation_id="stream.async",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def upload(
        self,
        *,
        body: Annotated[ReplayableBodyStream, ReplayableStreamBody()],
    ) -> dict[str, object]:
        raise NotImplementedError


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_http_method_round_trip_uses_the_real_httpx_transport(
    http_server: LocalHttpServer, method: str
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={}, follow_redirects=True)
    with client_from_httpx(raw) as client:
        response = client.request(method, "/echo")
    assert response.status_code == 200
    assert response.method == method
    assert http_server.exchanges[-1].method == method
    assert http_server.exchanges[-1].body == b""
    if method == "HEAD":
        assert response.body == b""
    else:
        assert response.json()["method"] == method


@pytest.mark.parametrize(
    ("argument", "value", "expected"),
    [
        ("json", None, b"null"),
        ("json", {"message": "привет"}, '{"message":"привет"}'.encode()),
        ("content", b"raw\x00body", b"raw\x00body"),
        ("content", b"", b""),
    ],
)
def test_body_variants_arrive_as_prepared_bytes(
    http_server: LocalHttpServer, argument: str, value: object, expected: bytes
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client:
        if argument == "json":
            response = client.request("POST", "/echo", json=value)
        else:
            response = client.request("POST", "/echo", content=cast(bytes, value))
    assert response.status_code == 200
    assert http_server.exchanges[-1].body == expected


def test_query_headers_and_manual_cookies_round_trip(
    http_server: LocalHttpServer,
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client:
        response = client.get(
            "/echo",
            params={"tag": "a,b", "empty": "", "unicode": "привет"},
            headers={"X-Test": "value"},
            cookies={"session": "token"},
        )
    payload = response.json()
    assert payload["query"] == [
        ["tag", "a,b"],
        ["empty", ""],
        ["unicode", "привет"],
    ]
    assert payload["headers"]["x-test"] == "value"
    assert payload["headers"]["cookie"] == "session=token"


def test_repeated_response_headers_and_streamed_origin_body_are_preserved(
    http_server: LocalHttpServer,
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client:
        headers = client.get("/headers")
        streamed = client.get("/stream")
    response_headers = cast_headers(headers.headers)
    assert response_headers.getall("x-duplicate") == ("a", "b")
    assert response_headers["x-test"] == "value"
    assert streamed.body == b"first-second-third"


def test_sync_httpx_handler_streams_replayable_request_without_buffering(
    http_server: LocalHttpServer,
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client:
        result = StreamApi(client).upload(
            body=ReplayableBodyStream(lambda: BytesIO(b"sync-stream"), known_length=11)
        )

    assert result["body"] == "sync-stream"
    assert http_server.exchanges[-1].body == b"sync-stream"


async def test_async_httpx_handler_streams_replayable_request_without_buffering(
    http_server: LocalHttpServer,
) -> None:
    raw = httpx.AsyncClient(base_url=http_server.url, headers={}, cookies={})
    async with client_from_httpx(raw) as client:
        result = await AsyncStreamApi(client).upload(
            body=ReplayableBodyStream(lambda: BytesIO(b"async-stream"), known_length=12)
        )

    assert result["body"] == "async-stream"
    assert http_server.exchanges[-1].body == b"async-stream"


def test_multiple_set_cookie_lines_are_available_without_loss(
    http_server: LocalHttpServer,
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client:
        response = client.get("/cookies/set")
    assert cast_headers(response.headers).getall("set-cookie") == (
        "first=one; Path=/",
        "second=two; Path=/",
    )


def test_invalid_body_combination_fails_before_network(http_server: LocalHttpServer) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client, pytest.raises(ValueError, match="mutually exclusive"):
        client.post("/echo", json={"a": 1}, content=b"body")
    assert http_server.exchanges == ()


@pytest.mark.parametrize("path", ["/disconnect", "/delay?seconds=0.2"])
def test_disconnect_and_timeout_are_normalized_as_transport_failures(
    http_server: LocalHttpServer, path: str
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client, pytest.raises(TransportFailure) as captured:
        client.get(path, options=CallOptions(timeout=0.05))
    assert captured.value.handler == "zapros"
    assert captured.value.phase == "emit"
    assert captured.value.__cause__ is not None
