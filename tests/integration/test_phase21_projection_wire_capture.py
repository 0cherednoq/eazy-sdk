from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import TypedDict, Unpack

import httpx
import pytest
from pytest_httpserver import HTTPServer

from eazy_sdk import AsyncApi, AsyncClient, ClientConfig, api
from eazy_sdk.crypto import (
    CryptoContext,
    FrozenValue,
    encrypt_field,
    encrypt_outbound,
    payload_crypto,
)
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import (
    BodyProjection,
    JsonBody,
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    header_output,
    hmac_sha256,
)
from eazy_sdk.response import Empty, Responses, Success


class CaptureSource(TypedDict):
    secret: str


class CaptureEnvelope(TypedDict):
    secret: str


class CaptureWire(TypedDict):
    envelope: CaptureEnvelope


def to_capture_wire(source: CaptureSource) -> CaptureWire:
    return {"envelope": {"secret": source["secret"]}}


@dataclass(frozen=True)
class CaptureCipher:
    name: str = "phase21-capture-test-only"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert context.attempt == 1
        assert isinstance(value, str)
        return "encrypted:" + value


@pytest.mark.integration
async def test_projected_crypto_and_signature_match_localhost_first_hop(
    httpserver: HTTPServer,
) -> None:
    key = SigningKeyRequirement("capture-key")
    projection = BodyProjection(
        CaptureSource,
        CaptureWire,
        to_capture_wire,
        JsonBody(),
    )
    crypto = payload_crypto(
        "capture",
        outbound=encrypt_outbound(
            encrypt_field(
                CaptureWire,
                lambda body: body["envelope"]["secret"],
                using=CaptureCipher(),
            )
        ),
    )
    signature = hmac_sha256(
        key=key,
        base=body_digest(),
        output=header_output("X-Capture-Signature"),
    )
    responses: Responses[None] = Responses(success=(Success(204, Empty()),))

    class CaptureApi(AsyncApi):
        @api.put(
            "/capture",
            responses=responses,
            body=projection,
            crypto=crypto,
            signing=signature,
        )
        async def capture(self, **request: Unpack[CaptureSource]) -> None:
            raise NotImplementedError

    expected_body = b'{"envelope":{"secret":"encrypted:visible"}}'
    expected_signature = hmac.new(
        b"capture-secret",
        hashlib.sha256(expected_body).hexdigest().encode(),
        hashlib.sha256,
    ).hexdigest()
    httpserver.expect_oneshot_request(
        "/capture",
        method="PUT",
        data=expected_body,
        headers={
            "Content-Type": "application/json",
            "X-Capture-Signature": expected_signature,
        },
    ).respond_with_data(status=204)

    raw = httpx.AsyncClient(headers={}, cookies={})
    async with AsyncClient(
        base_url=httpserver.url_for("/"),
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(
            key_provider=lambda _requirement: SigningKey(b"capture-secret")
        ),
    ) as client:
        await CaptureApi(client).capture(secret="visible")

    httpserver.check()
