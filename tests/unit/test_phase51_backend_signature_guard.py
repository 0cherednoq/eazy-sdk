"""Phase 51.5: a non-stdlib JSON backend and a signature over the body are refused together.

The request body is encoded by the operation's configured backend (``request/prepared.py``), but
the signature base built from the JSON view (``JsonProjection.build``) and the body rewrite after
inserting a signature output (``_apply_body_output``) both go through the module-level
``dump_json``, which is always the stdlib encoder (plan §1, F5). With one backend shipped today
that is invisible; a second backend that formats numbers, keys or whitespace differently would
sign bytes the server never receives, and the failure looks like an unrelated 401 from the far
side, not a bug report anyone can act on. The fix refuses the combination when the operation is
declared, not when a real byte mismatch happens to a real caller.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import Client, ClientConfig, Identity, SyncApi, api
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import (
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    body_output,
    canonical_json,
    header_output,
    hmac_sha256,
    path,
)
from eazy_sdk.request.markers import JsonBody
from eazy_sdk.request.wire import JsonPolicy
from eazy_sdk.response import Json, Responses, Success
from eazy_sdk.serialization import BackendCapabilityError, Serialization

BASE = "https://sign.test"
KEY = SigningKeyRequirement("sign")

READS_JSON_BODY = hmac_sha256(
    key=KEY, base=canonical_json(), output=header_output("X-Signature")
)
WRITES_JSON_BODY = hmac_sha256(key=KEY, base=path(), output=body_output("sig"))
NO_BODY_SIGNATURE = hmac_sha256(
    key=KEY, base=body_digest(), output=header_output("X-Signature")
)


class Receipt(BaseModel):
    ok: bool


class Payload(BaseModel):
    value: str
    sig: str = ""  # target for body_output("sig") in the write-side signature


RECEIPT: Responses[Receipt] = Responses(success=(Success(200, Json(Receipt)),))


@dataclass(frozen=True, slots=True)
class FakeFastJson:
    """A stand-in for a second JSON backend: same bytes as stdlib, different name."""

    name: str = "fake-fast"
    readable: bool = True

    def supports(self, policy: JsonPolicy) -> bool:
        return True

    def dumps(
        self,
        value: object,
        policy: JsonPolicy,
        *,
        default: Callable[[Any], Any] | None = None,
        sort_keys: bool | None = None,
    ) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=policy.ensure_ascii,
            separators=policy.separators,
            sort_keys=policy.sort_keys if sort_keys is None else sort_keys,
            default=default,
        ).encode("utf-8")

    def loads(self, data: bytes) -> object:
        return json.loads(data)


class SignedApi(SyncApi):
    @api.post(
        "/read",
        success=RECEIPT.success,
        errors=RECEIPT.errors,
        fallback=RECEIPT.fallback,
        signing=READS_JSON_BODY,
    )
    def read_signed(self, *, body: Annotated[Payload, JsonBody()]) -> Receipt:
        raise NotImplementedError

    @api.post(
        "/write",
        success=RECEIPT.success,
        errors=RECEIPT.errors,
        fallback=RECEIPT.fallback,
        signing=WRITES_JSON_BODY,
    )
    def write_signed(self, *, body: Annotated[Payload, JsonBody()]) -> Receipt:
        raise NotImplementedError

    @api.post(
        "/no-body-signature",
        success=RECEIPT.success,
        errors=RECEIPT.errors,
        fallback=RECEIPT.fallback,
        signing=NO_BODY_SIGNATURE,
    )
    def signed_elsewhere(self, *, body: Annotated[Payload, JsonBody()]) -> Receipt:
        raise NotImplementedError

    @api.post(
        "/unsigned",
        success=RECEIPT.success,
        errors=RECEIPT.errors,
        fallback=RECEIPT.fallback,
    )
    def unsigned(self, *, body: Annotated[Payload, JsonBody()]) -> Receipt:
        raise NotImplementedError


def _client() -> Client:
    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    raw = httpx.Client(transport=httpx.MockTransport(serve))
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True), config=ClientConfig())


def _identity() -> Identity:
    return Identity(key_provider=lambda _requirement: SigningKey(b"secret"))


def test_a_non_stdlib_backend_signing_the_json_read_side_is_refused() -> None:
    serialization = Serialization(json=FakeFastJson())

    with _client() as client, pytest.raises(BackendCapabilityError) as failure:
        SignedApi(client, identity=_identity(), serialization=serialization).read_signed(
            body=Payload(value="x")
        )
    message = str(failure.value)
    assert "read_signed" in message
    assert "fake-fast" in message


def test_a_non_stdlib_backend_signing_the_json_write_side_is_refused() -> None:
    serialization = Serialization(json=FakeFastJson())

    with _client() as client, pytest.raises(BackendCapabilityError) as failure:
        SignedApi(client, identity=_identity(), serialization=serialization).write_signed(
            body=Payload(value="x")
        )
    message = str(failure.value)
    assert "write_signed" in message
    assert "fake-fast" in message


def test_the_same_operation_compiles_with_the_stdlib_backend() -> None:
    with _client() as client:
        receipt = SignedApi(client, identity=_identity()).read_signed(body=Payload(value="x"))
    assert receipt.ok is True


def test_a_non_stdlib_backend_is_fine_when_no_signature_touches_the_body() -> None:
    """``body_digest``/header output never re-encode the body; only the JSON view does."""

    serialization = Serialization(json=FakeFastJson())

    with _client() as client:
        receipt = SignedApi(
            client, identity=_identity(), serialization=serialization
        ).signed_elsewhere(body=Payload(value="x"))
    assert receipt.ok is True


def test_a_non_stdlib_backend_is_fine_for_an_unsigned_operation() -> None:
    serialization = Serialization(json=FakeFastJson())

    with _client() as client:
        receipt = SignedApi(client, serialization=serialization).unsigned(body=Payload(value="x"))
    assert receipt.ok is True
