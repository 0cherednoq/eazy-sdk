from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Annotated

from eazy_sdk_html import CSS
from zapros import BaseHandler, Request, Response

from eazy_sdk import Client, ClientConfig, RetryPolicy, SyncApi, api
from eazy_sdk.request import (
    JsonBody,
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    header_output,
    hmac_sha256,
    join,
    target,
)
from eazy_sdk.response import Html, Json, Responses, Success

KEY = SigningKeyRequirement("phase18-signing")
SIGNATURE = hmac_sha256(
    key=KEY,
    base=join(target(), body_digest(), separator=b"\n"),
    output=header_output("X-Signature"),
)


class SignedApi(SyncApi):
    @api.post(
        "/signed",
        operation_id="phase18.signed",
        responses=Responses(success=(Success(200, Json(dict)),)),
        signing=SIGNATURE,
        idempotent=True,
    )
    def send(self, *, body: Annotated[dict[str, object], JsonBody()]) -> dict[str, object]:
        raise NotImplementedError


class RetrySigningHandler(BaseHandler):
    def __init__(self, keys: list[bytes]) -> None:
        self.keys = keys
        self.requests: list[Request] = []

    def handle(self, request: Request) -> Response:
        self.requests.append(request)
        key = self.keys[len(self.requests) - 1]
        assert isinstance(request.body, bytes)
        base = str(request.url).partition("https://example.test")[2].encode() + b"\n"
        base += hashlib.sha256(request.body).hexdigest().encode()
        assert request.headers["X-Signature"] == hmac.new(key, base, hashlib.sha256).hexdigest()
        status = 503 if len(self.requests) == 1 else 200
        return Response(
            status,
            [("Content-Type", "application/json")],
            content=b'{"ok":true}',
            request=request,
        )

    def close(self) -> None:
        return None


def test_retry_builds_and_signs_fresh_exact_body_through_zapros() -> None:
    keys: list[bytes] = []

    def key_provider(_requirement: SigningKeyRequirement) -> SigningKey:
        key = f"secret-{len(keys) + 1}".encode()
        keys.append(key)
        return SigningKey(key)

    handler = RetrySigningHandler(keys)
    config = ClientConfig(
        key_provider=key_provider,
        retry=RetryPolicy.safe(max_attempts=2),
        auth_retries=0,
    )

    with Client(base_url="https://example.test", handler=handler, config=config) as client:
        result = SignedApi(client).send(body={"z": 1, "a": 2})

    assert result == {"ok": True}
    assert keys == [b"secret-1", b"secret-2"]
    assert len(handler.requests) == 2
    assert handler.requests[0] is not handler.requests[1]
    assert [request.body for request in handler.requests] == [
        b'{"z":1,"a":2}',
        b'{"z":1,"a":2}',
    ]
    assert handler.requests[0].headers["X-Signature"] != handler.requests[1].headers["X-Signature"]


@dataclass
class PageTitle:
    title: Annotated[str, CSS("h1::text")]


class HtmlApi(SyncApi):
    @api.get(
        "/page",
        operation_id="phase18.html",
        responses=Responses(success=(Success(200, Html(PageTitle)),)),
    )
    def page(self) -> PageTitle:
        raise NotImplementedError


class HtmlHandler(BaseHandler):
    def handle(self, request: Request) -> Response:
        return Response(
            200,
            [("Content-Type", "text/html")],
            content=b"<html><h1>From Zapros</h1></html>",
            request=request,
        )

    def close(self) -> None:
        return None


def test_sdk_html_response_flows_through_fake_zapros_handler() -> None:
    with Client(base_url="https://example.test", handler=HtmlHandler()) as client:
        page = HtmlApi(client).page()

    assert page == PageTitle("From Zapros")
