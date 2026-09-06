"""Phase 49: protocol envelopes — the framing left the transport it never belonged to.

A JSON-RPC service is an ordinary HTTP service whose method name travels in the body. Before
this phase the SDK could only address by path and verb, so every such operation repeated the
same URL and hand-assembled `{"jsonrpc", "id", "method", "params"}` inside its business
projection — which put the envelope, and therefore the thing a signature covers, out of sight
of the declaration.

These tests hold the two claims that make it worth doing: the envelope is a stage of the
representation pipeline (so signing and encryption see it), and it is not a second SDK (no
second decorator family, no second error path).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Any

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncClient, Identity, api
from eazy_sdk.core.kernel import Malformed, ParsedValue
from eazy_sdk.crypto import encrypt_encoded, encrypt_outbound, http_encrypted, payload_crypto
from eazy_sdk.crypto.core import CryptoContext
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.protocols import (
    ChannelKey,
    ControlKind,
    CorrelationKey,
    Envelope,
    InboundMessageKind,
    JsonRpc,
    ProtocolMessage,
    rpc_error,
    rpc_error_default,
    rpc_result,
)
from eazy_sdk.protocols.jsonrpc import RpcEnvelopeError
from eazy_sdk.request import (
    SigningKey,
    SigningKeyRequirement,
    Wire,
    body_digest,
    header_output,
    hmac_sha256,
)
from eazy_sdk.request.markers import JsonField
from eazy_sdk.request.pipeline import REQUEST_PIPELINE, RequestStage
from eazy_sdk.response import Json, MalformedResponseError, Responses
from eazy_sdk.response.cases import ApiError

pytestmark = pytest.mark.unit

BASE = "https://rpc.test"
KEY = SigningKeyRequirement("phase49")
SECRET = Identity(key_provider=lambda _requirement: SigningKey(b"secret"))


class Receipt(BaseModel):
    reference: str


class Fault(BaseModel):
    code: int
    message: str


CHARGE: Responses[Receipt] = Responses(
    success=(rpc_result(Receipt),),
    errors=(rpc_error(-32001, Fault),),
    fallback=rpc_error_default(Fault),
)


class BillingService:
    base_url = BASE
    protocol = JsonRpc(path="/endpoint")


class BillingApi(BillingService, AsyncApi):
    @api.rpc(
        "account.charge",
        success=CHARGE.success,
        errors=CHARGE.errors,
        fallback=CHARGE.fallback,
    )
    async def charge(
        self,
        *,
        account: Annotated[str, JsonField()],
        amount: Annotated[int, JsonField()],
    ) -> Receipt:
        raise NotImplementedError

    @api.get("/health", success=Json(dict[str, str]))
    async def health(self) -> dict[str, str]:
        raise NotImplementedError


def _client(handler: Any) -> AsyncClient:
    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return AsyncClient(base_url=BASE, handler=AsyncHttpxHandler(raw, owns_client=True))


def _reply(request: httpx.Request, **body: Any) -> httpx.Response:
    sent = json.loads(request.content)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": sent["id"], **body})


# --- the envelope is a pipeline stage ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_envelope_wraps_the_payload_and_takes_the_url_from_the_service() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _reply(request, result={"reference": "r-1"})

    async with _client(handler) as client:
        receipt = await BillingApi(client).charge(account="a-1", amount=1000)

    assert receipt.reference == "r-1"
    request = captured[0]
    assert request.url.path == "/endpoint", "the path came from the service protocol"
    assert request.method == "POST"
    sent = json.loads(request.content)
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "account.charge"
    assert sent["params"] == {"account": "a-1", "amount": 1000}
    assert sent["id"], "the envelope carries a correlation"


@pytest.mark.asyncio
async def test_the_envelope_is_a_declared_stage_between_projection_and_crypto() -> None:
    seen: list[tuple[RequestStage, ...]] = []

    def observer(phase: str, value: object) -> None:
        if phase == "stages":
            assert isinstance(value, tuple)
            seen.append(value)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return _reply(request, result={"reference": "r-1"})

    async with _client(handler) as client:
        identity = Identity(observer=observer)
        await BillingApi(client, identity=identity).charge(account="a", amount=1)
        await BillingApi(client, identity=identity).health()

    rpc_stages, rest_stages = seen
    assert RequestStage.ENVELOPE in rpc_stages
    assert list(rpc_stages) == [stage for stage in REQUEST_PIPELINE if stage in rpc_stages]
    assert RequestStage.ENVELOPE not in rest_stages, "a REST operation runs no envelope stage"


@pytest.mark.asyncio
async def test_a_rest_operation_next_to_an_envelope_service_goes_out_unwrapped() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"status": "ok"})

    async with _client(handler) as client:
        assert await BillingApi(client).health() == {"status": "ok"}

    assert captured[0].url.path == "/health", "the health check kept its own path"


@pytest.mark.asyncio
async def test_the_signature_and_the_encryption_cover_the_whole_envelope() -> None:
    class WholeCipher:
        name = "phase49-test-only"

        def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
            return b"sealed:" + value

    crypto = payload_crypto(
        "phase49", outbound=encrypt_outbound(encoded=encrypt_encoded(using=WholeCipher()))
    )

    class SealedApi(BillingService, AsyncApi):
        @api.rpc(
            "account.charge",
            success=CHARGE.success,
            errors=CHARGE.errors,
            fallback=CHARGE.fallback,
            crypto=crypto,
            signing=hmac_sha256(key=KEY, base=body_digest(), output=header_output("X-Sig")),
            wire=Wire(encrypted=http_encrypted(content_type="application/sealed+json")),
        )
        async def charge(
            self,
            *,
            account: Annotated[str, JsonField()],
        ) -> Receipt:
            raise NotImplementedError

    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        inner = json.loads(request.content.removeprefix(b"sealed:"))
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": inner["id"], "result": {"reference": "r-1"}}
        )

    async with _client(handler) as client:
        await SealedApi(client, identity=SECRET).charge(account="a-1")

    request = captured[0]
    envelope = json.loads(request.content.removeprefix(b"sealed:"))
    assert envelope["method"] == "account.charge", "the envelope was built, then encrypted"
    expected = hmac.new(
        b"secret", hashlib.sha256(request.content).hexdigest().encode(), hashlib.sha256
    ).hexdigest()
    assert request.headers["x-sig"] == expected, "the signature covers the sealed envelope"


# --- correlation and repeats -------------------------------------------------------------


class Reused(BillingService, AsyncApi):
    # A JSON-RPC call that reuses its id is safe to repeat: that is what the id is for.
    @api.rpc(
        "account.charge",
        success=CHARGE.success,
        errors=CHARGE.errors,
        fallback=CHARGE.fallback,
        idempotent=True,
    )
    async def charge(self, *, account: Annotated[str, JsonField()]) -> Receipt:
        raise NotImplementedError


class RegeneratedService:
    base_url = BASE
    protocol = JsonRpc(path="/endpoint", id_on_retry="regenerate")


class Regenerated(RegeneratedService, AsyncApi):
    @api.rpc(
        "account.charge",
        success=CHARGE.success,
        errors=CHARGE.errors,
        fallback=CHARGE.fallback,
        idempotent=True,
    )
    async def charge(self, *, account: Annotated[str, JsonField()]) -> Receipt:
        raise NotImplementedError


async def _ids_over_a_retry(router: type[Any]) -> list[str]:
    from eazy_sdk import ClientConfig, Resilience
    from eazy_sdk.clients import RetryPolicy

    ids: list[str] = []
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        sent = json.loads(request.content)
        ids.append(sent["id"])
        if attempts == 1:
            return httpx.Response(503)
        return _reply(request, result={"reference": "r-1"})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={}, cookies={})
    config = ClientConfig(resilience=Resilience(retry=RetryPolicy.safe(max_attempts=2)))
    async with AsyncClient(
        base_url=BASE, handler=AsyncHttpxHandler(raw, owns_client=True), config=config
    ) as client:
        await router(client).charge(account="a-1")
    return ids


@pytest.mark.asyncio
async def test_a_repeat_reuses_the_correlation_by_default() -> None:
    ids = await _ids_over_a_retry(Reused)
    assert len(ids) == 2
    assert ids[0] == ids[1], "the server can recognise the repeat as a duplicate"


@pytest.mark.asyncio
async def test_a_service_that_rejects_a_repeated_id_declares_so_and_gets_a_new_one() -> None:
    ids = await _ids_over_a_retry(Regenerated)
    assert len(ids) == 2
    assert ids[0] != ids[1]


@pytest.mark.asyncio
async def test_a_reply_that_answers_a_different_request_is_malformed() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "somebody-elses", "result": {"reference": "r"}}
        )

    async with _client(handler) as client:
        with pytest.raises(MalformedResponseError) as failure:
            await BillingApi(client).charge(account="a", amount=1)
    assert "somebody-elses" in str(failure.value)


# --- errors go through the existing path --------------------------------------------------


@pytest.mark.asyncio
async def test_a_200_with_an_error_member_reaches_the_typed_model() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _reply(request, error={"code": -32001, "message": "no funds"})

    async with _client(handler) as client:
        with pytest.raises(ApiError) as failure:
            await BillingApi(client).charge(account="a", amount=1)
    fault = failure.value.error
    assert isinstance(fault, Fault)
    assert (fault.code, fault.message) == (-32001, "no funds")


@pytest.mark.asyncio
async def test_an_unlisted_code_falls_to_the_declared_default_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _reply(request, error={"code": -32603, "message": "internal"})

    async with _client(handler) as client:
        with pytest.raises(ApiError) as failure:
            await BillingApi(client).charge(account="a", amount=1)
    assert failure.value.error.code == -32603


def test_no_second_error_path_and_no_second_decorator_family_exist() -> None:
    import eazy_sdk
    import eazy_sdk.protocols as protocols

    for name in ("json_rpc_error_raiser", "error_raiser", "JsonRPCBuilder", "RpcBuilder"):
        assert not hasattr(protocols, name), name
        assert not hasattr(eazy_sdk, name), name
    assert "rpc" in dir(api), "the RPC operation is a member of the one decorator namespace"
    assert not hasattr(eazy_sdk, "rpc")


def test_declaring_an_rpc_operation_without_a_protocol_fails_at_declaration_time() -> None:
    with pytest.raises(TypeError, match="declares no protocol envelope"):

        class Orphan(AsyncApi):
            @api.rpc(
                "account.charge",
                success=CHARGE.success,
                errors=CHARGE.errors,
                fallback=CHARGE.fallback,
            )
            async def charge(self, *, account: Annotated[str, JsonField()]) -> Receipt:
                raise NotImplementedError


# --- the envelope is transport-neutral and pure -------------------------------------------


def test_the_envelope_concepts_live_outside_the_transport_package() -> None:
    import eazy_sdk.websocket as websocket

    for name in ("ProtocolMessage", "CorrelationKey", "ChannelKey", "InboundMessageKind"):
        assert not hasattr(websocket, name), f"{name} is no longer a WebSocket-only concept"
    from eazy_sdk.websocket.protocols import WsProtocol

    assert Envelope in WsProtocol.__mro__, "the WebSocket protocol extends the shared base"


def test_the_json_rpc_envelope_is_a_pure_function_with_no_transport() -> None:
    from eazy_sdk.crypto import freeze_value, thaw_value

    envelope = JsonRpc(path="/endpoint")
    built = thaw_value(
        envelope.build_outbound(
            "account.charge", freeze_value({"account": "a"}), correlation=CorrelationKey("id-1")
        )
    )
    assert built == {
        "jsonrpc": "2.0",
        "id": "id-1",
        "method": "account.charge",
        "params": {"account": "a"},
    }

    read = envelope.read(freeze_value({"jsonrpc": "2.0", "id": "id-1", "result": {"ok": True}}))
    assert isinstance(read, ParsedValue)
    message = read.value
    assert isinstance(message, ProtocolMessage)
    assert message.kind is InboundMessageKind.REPLY
    assert message.correlation == CorrelationKey("id-1")
    assert thaw_value(message.payload) == {"ok": True}

    broken = envelope.read(freeze_value({"jsonrpc": "2.0", "id": "id-1"}))
    assert isinstance(broken, Malformed)
    assert isinstance(broken.cause, RpcEnvelopeError)

    assert ChannelKey("c").value == "c"
    assert ControlKind.PING.value == "ping"
