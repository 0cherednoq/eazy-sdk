from __future__ import annotations

import copy
import hashlib
import hmac
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from pyreqwest.client import ClientBuilder as PyreqwestClientBuilder
from pyreqwest.client import SyncClientBuilder as SyncPyreqwestClientBuilder
from pywhatwgurl import URL
from zapros import (
    AsyncBaseHandler,
    AsyncClient,
    AsyncPyreqwestHandler,
    AsyncStdNetworkHandler,
    BaseHandler,
    Client,
    Multipart,
    Part,
    PyreqwestHandler,
    Request,
    Response,
    StdNetworkHandler,
)

from eazy_sdk.handlers.curl_cffi import (
    AsyncCurlCffiZaprosHandler,
    CurlCffiZaprosHandler,
)
from tests._support.http_server import CapturedExchange, LocalHttpServer

_KEY = b"zapros-evaluation-key"


class RecordingHandler(BaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def handle(self, request: Request) -> Response:
        self.requests.append(request)
        return Response(200, content=b"ok", request=request)

    def close(self) -> None:
        return None


class AsyncRecordingHandler(AsyncBaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    async def ahandle(self, request: Request) -> Response:
        self.requests.append(request)
        return Response(200, content=b"ok", request=request)

    async def aclose(self) -> None:
        return None


class SigningHandler(BaseHandler):
    """Sign the final bytes produced by Zapros before terminal transport translation."""

    def __init__(self, next_handler: BaseHandler) -> None:
        self._next = next_handler

    def handle(self, request: Request) -> Response:
        request.headers["X-Signature"] = _sign_request(request)
        return self._next.handle(request)

    def close(self) -> None:
        self._next.close()


class AsyncSigningHandler(AsyncBaseHandler):
    def __init__(self, next_handler: AsyncBaseHandler) -> None:
        self._next = next_handler

    async def ahandle(self, request: Request) -> Response:
        request.headers["X-Signature"] = _sign_request(request)
        return await self._next.ahandle(request)

    async def aclose(self) -> None:
        await self._next.aclose()


class StripZaprosDefaults(BaseHandler):
    def __init__(self, next_handler: BaseHandler) -> None:
        self._next = next_handler

    def handle(self, request: Request) -> Response:
        for name in ("Accept", "Accept-Encoding", "User-Agent"):
            del request.headers[name]
        return self._next.handle(request)

    def close(self) -> None:
        self._next.close()


@dataclass(frozen=True)
class BodyCase:
    name: str
    kwargs: dict[str, object]
    expected: bytes
    content_type: str | None


BODY_CASES = (
    BodyCase("empty", {}, b"", None),
    BodyCase(
        "json",
        {"json": {"z": 1, "a": "тест"}},
        '{"z":1,"a":"тест"}'.encode(),
        "application/json",
    ),
    BodyCase(
        "form",
        {"form": [("z", "two words"), ("a", "1")]},
        b"z=two+words&a=1",
        "application/x-www-form-urlencoded",
    ),
    BodyCase("raw", {"body": b"\x00raw\xff"}, b"\x00raw\xff", None),
    BodyCase(
        "multipart",
        {
            "multipart": Multipart("eazy_sdk-fixed-boundary")
            .text("first", "one")
            .part("file", Part.bytes(b"payload").file_name("sample.bin"))
        },
        (
            b"--eazy_sdk-fixed-boundary\r\n"
            b'Content-Disposition: form-data; name="first"\r\n'
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"one\r\n"
            b"--eazy_sdk-fixed-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="sample.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            b"payload\r\n"
            b"--eazy_sdk-fixed-boundary--\r\n"
        ),
        'multipart/form-data; boundary="eazy_sdk-fixed-boundary"',
    ),
)


def test_zapros_keeps_unique_query_order_and_does_not_mutate_inputs() -> None:
    recorder = RecordingHandler()
    params = [("first", "one"), ("space", "two words"), ("last", "три")]
    headers = {"X-First": "one", "X-Second": "two"}
    payload = {"z": 1, "a": {"nested": [1, 2]}}
    original = copy.deepcopy((params, headers, payload))

    with Client(handler=recorder) as client:
        client.post(
            "https://example.test/items",
            params=params,
            headers=headers,
            json=payload,
        )

    assert (params, headers, payload) == original
    request = recorder.requests[0]
    assert str(request.url) == (
        "https://example.test/items?first=one&space=two+words&last=%D1%82%D1%80%D0%B8"
    )
    assert request.body == b'{"z":1,"a":{"nested":[1,2]}}'
    assert list(request.headers)[:2] == ["X-First", "X-Second"]


def test_duplicate_query_keys_are_explicitly_out_of_scope_for_the_zapros_boundary() -> None:
    recorder = RecordingHandler()
    with Client(handler=recorder) as client:
        client.get("https://example.test/items", params=[("tag", "a"), ("tag", "b")])

    # Zapros 0.16.0 collapses duplicate query keys to their first value. Eazy SDK therefore
    # declares duplicate query names unsupported instead of pretending to preserve them.
    assert str(recorder.requests[0].url) == "https://example.test/items?tag=a"


def test_zapros_default_headers_can_be_overridden_or_deleted_after_building() -> None:
    defaults = RecordingHandler()
    with Client(handler=defaults) as client:
        client.post("https://example.test/items", json={"ok": True})
    request = defaults.requests[0]
    assert request.headers["Host"] == "example.test"
    assert request.headers["Accept"] == "*/*"
    assert request.headers["User-Agent"] == "python-zapros/0.16.0"
    assert request.headers["Accept-Encoding"]
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Content-Length"] == str(len(b'{"ok":true}'))

    explicit = RecordingHandler()
    with Client(handler=explicit) as client:
        client.post(
            "https://example.test/items",
            headers={
                "Accept": "application/vnd.test+json",
                "Accept-Encoding": "identity",
                "User-Agent": "private-sdk/1",
                "Content-Type": "application/vnd.test+json",
            },
            json={"ok": True},
        )
    overridden = explicit.requests[0]
    assert overridden.headers["Accept"] == "application/vnd.test+json"
    assert overridden.headers["Accept-Encoding"] == "identity"
    assert overridden.headers["User-Agent"] == "private-sdk/1"
    assert overridden.headers["Content-Type"] == "application/vnd.test+json"

    stripped = RecordingHandler()
    with Client(handler=StripZaprosDefaults(stripped)) as client:
        client.get("https://example.test/items")
    assert "Accept" not in stripped.requests[0].headers
    assert "Accept-Encoding" not in stripped.requests[0].headers
    assert "User-Agent" not in stripped.requests[0].headers


@pytest.mark.parametrize("case", BODY_CASES, ids=lambda case: case.name)
def test_curl_cffi_handler_sends_all_buffered_zapros_body_kinds(
    http_server: LocalHttpServer, case: BodyCase
) -> None:
    handler = CurlCffiZaprosHandler()
    with Client(handler=handler) as client:
        cast(Any, client.request)("POST", f"{http_server.url}/echo", **case.kwargs)

    exchange = http_server.exchanges[-1]
    assert exchange.body == case.expected
    headers = {name.casefold(): value for name, value in exchange.headers}
    if case.content_type is None:
        assert "content-type" not in headers
    else:
        assert headers["content-type"] == case.content_type


def test_curl_cffi_handler_sends_text_and_rejects_stream_without_buffering(
    http_server: LocalHttpServer,
) -> None:
    handler = CurlCffiZaprosHandler()
    text_request = Request(URL(f"{http_server.url}/echo"), "POST", text="Привет")
    handler.handle(text_request).read()

    def stream() -> Iterator[bytes]:
        yield b"first-"
        yield b"second"

    with (
        Client(handler=handler) as client,
        pytest.raises(TypeError, match="does not support streaming request bodies"),
    ):
        client.post(f"{http_server.url}/echo", body=stream())

    text = http_server.exchanges[-1]
    assert text.body == "Привет".encode()
    assert dict((name.casefold(), value) for name, value in text.headers)["content-type"] == (
        "text/plain; charset=utf-8"
    )


def test_curl_cffi_handler_can_suppress_optional_zapros_default_headers(
    http_server: LocalHttpServer,
) -> None:
    terminal = CurlCffiZaprosHandler()
    with Client(handler=StripZaprosDefaults(terminal)) as client:
        client.get(f"{http_server.url}/echo")

    names = {name.casefold() for name, _ in http_server.exchanges[-1].headers}
    assert "host" in names
    assert "accept" not in names
    assert "accept-encoding" not in names
    assert "user-agent" not in names


def test_curl_cffi_preserves_unique_query_body_and_custom_header_order(
    http_server: LocalHttpServer,
) -> None:
    handler = CurlCffiZaprosHandler()
    with Client(handler=handler) as client:
        client.post(
            f"{http_server.url}/echo",
            params=[("z", "1"), ("a", "2"), ("m", "3")],
            headers={"X-Z": "first", "X-A": "second", "X-M": "third"},
            json={"z": 1, "a": 2, "m": 3},
        )

    exchange = http_server.exchanges[-1]
    assert exchange.target == "/echo?z=1&a=2&m=3"
    assert exchange.body == b'{"z":1,"a":2,"m":3}'
    names = [name.casefold() for name, _ in exchange.headers]
    assert names.index("x-z") < names.index("x-a") < names.index("x-m")


def _pyreqwest_handler() -> BaseHandler:
    builder = SyncPyreqwestClientBuilder().follow_redirects(False).default_cookie_store(False)
    return PyreqwestHandler(builder)


def _async_pyreqwest_handler() -> AsyncBaseHandler:
    builder = PyreqwestClientBuilder().follow_redirects(False).default_cookie_store(False)
    return AsyncPyreqwestHandler(builder)


SYNC_HANDLER_FACTORIES: tuple[tuple[str, Callable[[], BaseHandler]], ...] = (
    ("zapros-stdlib", StdNetworkHandler),
    ("zapros-pyreqwest", _pyreqwest_handler),
    ("curl-cffi-custom", CurlCffiZaprosHandler),
)

ASYNC_HANDLER_FACTORIES: tuple[tuple[str, Callable[[], AsyncBaseHandler]], ...] = (
    ("zapros-stdlib", AsyncStdNetworkHandler),
    ("zapros-pyreqwest", _async_pyreqwest_handler),
    ("curl-cffi-custom", AsyncCurlCffiZaprosHandler),
)


@pytest.mark.parametrize(
    ("_name", "handler_factory"), SYNC_HANDLER_FACTORIES, ids=[x[0] for x in SYNC_HANDLER_FACTORIES]
)
def test_explicit_headers_reach_wire_on_every_sync_transport(
    http_server: LocalHttpServer,
    _name: str,
    handler_factory: Callable[[], BaseHandler],
) -> None:
    with Client(handler=handler_factory()) as client:
        client.post(
            f"{http_server.url}/echo",
            headers={
                "X-First": "one",
                "X-Second": "two",
                "Accept": "application/vnd.eazy-sdk+json",
                "Accept-Encoding": "identity",
                "User-Agent": "eazy_sdk-zapros-evaluation/1",
                "Content-Type": "application/vnd.eazy-sdk+json",
            },
            body=b"body",
        )

    exchange = http_server.exchanges[-1]
    headers = {name.casefold(): value for name, value in exchange.headers}
    assert headers["accept"] == "application/vnd.eazy-sdk+json"
    assert headers["accept-encoding"] == "identity"
    assert headers["user-agent"] == "eazy_sdk-zapros-evaluation/1"
    assert headers["content-type"] == "application/vnd.eazy-sdk+json"
    names = [name.casefold() for name, _ in exchange.headers]
    assert names.index("x-first") < names.index("x-second")


@pytest.mark.parametrize(
    ("_name", "handler_factory"), SYNC_HANDLER_FACTORIES, ids=[x[0] for x in SYNC_HANDLER_FACTORIES]
)
def test_optional_zapros_defaults_can_be_absent_on_every_sync_transport(
    http_server: LocalHttpServer,
    _name: str,
    handler_factory: Callable[[], BaseHandler],
) -> None:
    with Client(handler=StripZaprosDefaults(handler_factory())) as client:
        client.get(f"{http_server.url}/echo")

    headers = {name.casefold(): value for name, value in http_server.exchanges[-1].headers}
    if _name == "zapros-pyreqwest":
        # Zapros removed its own values, but pyreqwest 0.12.4 added native defaults.
        assert headers["accept"] == "*/*"
        assert headers["user-agent"] == "python-pyreqwest/1.0.0"
        assert "accept-encoding" not in headers
        return
    names = set(headers)
    assert "accept" not in names
    assert "accept-encoding" not in names
    assert "user-agent" not in names


@pytest.mark.parametrize(
    ("_name", "handler_factory"), SYNC_HANDLER_FACTORIES, ids=[x[0] for x in SYNC_HANDLER_FACTORIES]
)
def test_terminal_handlers_do_not_add_redirect_cookie_or_retry_semantics(
    http_server: LocalHttpServer,
    _name: str,
    handler_factory: Callable[[], BaseHandler],
) -> None:
    with Client(handler=handler_factory()) as client:
        redirect = client.get(f"{http_server.url}/redirect/302?to=/echo")
        client.get(f"{http_server.url}/cookies/set")
        cookies = client.get(f"{http_server.url}/cookies/show")
        unavailable = client.get(f"{http_server.url}/status/503")

    assert redirect.status == 302
    assert cookies.json["cookie"] == ""
    assert unavailable.status == 503
    assert [exchange.target for exchange in http_server.exchanges] == [
        "/redirect/302?to=%2Fecho",
        "/cookies/set",
        "/cookies/show",
        "/status/503",
    ]


def test_default_pyreqwest_handler_follows_redirect_without_zapros_middleware(
    http_server: LocalHttpServer,
) -> None:
    with Client(handler=PyreqwestHandler()) as client:
        response = client.get(f"{http_server.url}/redirect/302?to=/echo")

    assert response.status == 200
    assert [exchange.target for exchange in http_server.exchanges] == [
        "/redirect/302?to=%2Fecho",
        "/echo",
    ]


@pytest.mark.parametrize(
    ("_name", "handler_factory"), SYNC_HANDLER_FACTORIES, ids=[x[0] for x in SYNC_HANDLER_FACTORIES]
)
def test_both_signature_strategies_match_wire_bytes_on_every_sync_transport(
    http_server: LocalHttpServer,
    _name: str,
    handler_factory: Callable[[], BaseHandler],
) -> None:
    terminal = handler_factory()
    raw_body = b'{"a": 1}'
    with Client(handler=terminal) as client:
        raw_target = "/echo?mode=raw"
        raw_signature = _sign_parts("POST", raw_target, raw_body)
        client.post(
            f"{http_server.url}/echo",
            params={"mode": "raw"},
            headers={"X-Signature": raw_signature},
            body=raw_body,
        )
        client.post(
            f"{http_server.url}/echo",
            params={"mode": "zapros"},
            json={"a": 1},
            handler=lambda handler: SigningHandler(handler),
        )

    raw_exchange, zapros_exchange = http_server.exchanges[-2:]
    _assert_exchange_signature(raw_exchange)
    _assert_exchange_signature(zapros_exchange)
    assert raw_exchange.body == b'{"a": 1}'
    assert zapros_exchange.body == b'{"a":1}'


@pytest.mark.parametrize(
    ("_name", "handler_factory"),
    ASYNC_HANDLER_FACTORIES,
    ids=[x[0] for x in ASYNC_HANDLER_FACTORIES],
)
async def test_both_signature_strategies_match_wire_bytes_on_every_async_transport(
    http_server: LocalHttpServer,
    _name: str,
    handler_factory: Callable[[], AsyncBaseHandler],
) -> None:
    terminal = handler_factory()
    raw_body = b'{"a": 1}'
    async with AsyncClient(handler=terminal) as client:
        raw_signature = _sign_parts("POST", "/echo?mode=raw-async", raw_body)
        await client.post(
            f"{http_server.url}/echo",
            params={"mode": "raw-async"},
            headers={"X-Signature": raw_signature},
            body=raw_body,
        )
        await client.post(
            f"{http_server.url}/echo",
            params={"mode": "zapros-async"},
            json={"a": 1},
            handler=lambda handler: AsyncSigningHandler(handler),
        )

    raw_exchange, zapros_exchange = http_server.exchanges[-2:]
    _assert_exchange_signature(raw_exchange)
    _assert_exchange_signature(zapros_exchange)
    assert raw_exchange.body == b'{"a": 1}'
    assert zapros_exchange.body == b'{"a":1}'


def test_signing_different_json_representation_is_observably_invalid() -> None:
    recorder = RecordingHandler()
    wrong = _sign_parts("POST", "/items", b'{"a": 1}')
    with Client(handler=recorder) as client:
        client.post(
            "https://example.test/items",
            headers={"X-Signature": wrong},
            json={"a": 1},
        )

    request = recorder.requests[0]
    assert request.body == b'{"a":1}'
    assert request.headers["X-Signature"] != _sign_request(request)


async def test_async_curl_cffi_rejects_async_stream_without_buffering(
    http_server: LocalHttpServer,
) -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield b"async-"
        yield b"stream"

    handler = AsyncCurlCffiZaprosHandler()
    async with AsyncClient(handler=handler) as client:
        with pytest.raises(TypeError, match="does not support async streaming request bodies"):
            await client.post(f"{http_server.url}/echo", body=stream())


def _sign_request(request: Request) -> str:
    if not isinstance(request.body, bytes):
        raise TypeError("evaluation signer supports only buffered Zapros bodies")
    split = urlsplit(str(request.url))
    target = split.path + (f"?{split.query}" if split.query else "")
    return _sign_parts(request.method, target, request.body)


def _sign_parts(method: str, target: str, body: bytes) -> str:
    body_digest = hashlib.sha256(body).hexdigest().encode()
    base = b"\n".join((method.encode(), target.encode(), body_digest))
    return hmac.new(_KEY, base, hashlib.sha256).hexdigest()


def _assert_exchange_signature(exchange: CapturedExchange) -> None:
    headers = {name.casefold(): value for name, value in exchange.headers}
    assert headers["x-signature"] == _sign_parts(exchange.method, exchange.target, exchange.body)
