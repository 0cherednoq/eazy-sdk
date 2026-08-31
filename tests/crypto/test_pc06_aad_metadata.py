from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, cast

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import AsyncApi, ClientConfig, SyncApi, api
from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoContext,
    CryptoDirection,
    CryptoInputScope,
    CryptoOutputValue,
    CryptoResult,
    CryptoStage,
    FrozenValue,
    HttpCryptoContext,
    PayloadEncryptionError,
    WebSocketCryptoContext,
    crypto_input,
    crypto_output,
    crypto_result,
    crypto_value,
    decrypt_encoded,
    decrypt_field,
    decrypt_inbound,
    encrypt_encoded,
    encrypt_field,
    encrypt_outbound,
    freeze_value,
    http_crypto_header,
    http_encrypted,
    payload_crypto,
    thaw_value,
    websocket_crypto_field,
    websocket_encrypted,
)
from eazy_sdk.crypto._inputs import resolve_crypto_inputs
from eazy_sdk.crypto._runtime import compile_payload_crypto, encrypt_bytes
from eazy_sdk.dependencies import DependencyContext, DependencyRegistry, RequestDependency
from eazy_sdk.models import default_model_adapters
from eazy_sdk.request import (
    JsonBody,
    SigningKeyRequirement,
    header_output,
    hmac_sha256,
    method,
)
from eazy_sdk.response import Json, Responses
from eazy_sdk.websocket import InboundMessageKind, ProtocolMessage
from eazy_sdk.websocket._artifacts import MessageReservedOutput
from eazy_sdk.websocket._crypto import (
    apply_ws_crypto_metadata,
    protect_ws_document,
    unprotect_ws_message,
)
from eazy_sdk.websocket.runtime import _validate_websocket_crypto
from tests._support.zapros_clients import client_from_httpx

TENANT_DEPENDENCY = cast(
    RequestDependency[str],
    RequestDependency.typed("tenant", str, secret=True),
)
TENANT = crypto_input(TENANT_DEPENDENCY, name="tenant-id")
CONNECTION_TENANT = crypto_input(
    TENANT_DEPENDENCY,
    name="tenant-id",
    scope=CryptoInputScope.CONNECTION,
)
KEY_ID = crypto_output("key-id", str)


@dataclass(frozen=True)
class TenantProvider:
    value: str

    def resolve(self, context: DependencyContext) -> str:
        return self.value


@dataclass(frozen=True)
class MetadataCipher:
    name: str = "metadata-test-only"

    def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes | CryptoResult[bytes]:
        tenant = context.input(TENANT)
        assert context.aad == (("tenant-id", "acme"),)
        return crypto_result(
            b"encrypted:" + value,
            crypto_value(KEY_ID, f"{tenant}-2026"),
        )

    def decrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        assert context.input(TENANT) == "acme"
        assert context.aad == (("tenant-id", "acme"),)
        assert context.metadata(KEY_ID) == "acme-2026"
        return value.removeprefix(b"encrypted:")


METADATA_CIPHER = MetadataCipher()
HTTP_PROFILE = payload_crypto(
    "metadata-http-v1",
    outbound=encrypt_outbound(encoded=encrypt_encoded(using=METADATA_CIPHER, outputs=(KEY_ID,))),
    inbound=decrypt_inbound(encoded=decrypt_encoded(using=METADATA_CIPHER, metadata=(KEY_ID,))),
    inputs=(TENANT,),
)
HTTP_WIRE = http_encrypted(
    content_type="application/vnd.example.encrypted+json",
    metadata=(http_crypto_header(KEY_ID, "X-Key-Id"),),
)


class Payload(BaseModel):
    value: str


RESPONSES = Responses[Payload](success={200: Json(Payload)})


class AsyncMetadataApi(AsyncApi):
    @api.post("/metadata", responses=RESPONSES, crypto=HTTP_PROFILE, crypto_wire=HTTP_WIRE)
    async def send(self, body: Annotated[Payload, JsonBody()]) -> Payload:
        raise AssertionError


class SyncMetadataApi(SyncApi):
    @api.post("/metadata", responses=RESPONSES, crypto=HTTP_PROFILE, crypto_wire=HTTP_WIRE)
    def send(self, body: Annotated[Payload, JsonBody()]) -> Payload:
        raise AssertionError


SIGNING_KEY = SigningKeyRequirement("pc06-collision-test")


class SigningCollisionApi(AsyncApi):
    @api.post(
        "/metadata",
        responses=RESPONSES,
        crypto=HTTP_PROFILE,
        crypto_wire=HTTP_WIRE,
        signing=(
            hmac_sha256(
                key=SIGNING_KEY,
                base=method(),
                output=header_output("X-Key-Id"),
            ),
        ),
    )
    async def send(self, body: Annotated[Payload, JsonBody()]) -> Payload:
        raise AssertionError


def _dependencies() -> DependencyRegistry:
    registry = DependencyRegistry()
    registry.register(TENANT_DEPENDENCY, TenantProvider("acme"))
    return registry


def _response(request: httpx.Request) -> httpx.Response:
    assert request.headers["x-key-id"] == "acme-2026"
    assert request.content.startswith(b"encrypted:")
    return httpx.Response(
        200,
        content=b'encrypted:{"value":"response"}',
        headers={"Content-Type": HTTP_WIRE.content_type, "X-Key-Id": "acme-2026"},
    )


@pytest.mark.asyncio
async def test_http_async_resolves_typed_aad_and_binds_declared_metadata() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _response(request)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
        ),
        config=ClientConfig(dependencies=_dependencies()),
    )
    try:
        result = await AsyncMetadataApi(client).send(Payload(value="request"))
    finally:
        await client.aclose()
    assert result == Payload(value="response")


def test_http_sync_uses_the_same_typed_aad_and_metadata_contract() -> None:
    client = client_from_httpx(
        httpx.Client(
            base_url="https://api.example",
            transport=httpx.MockTransport(_response),
        ),
        config=ClientConfig(dependencies=_dependencies()),
    )
    try:
        result = SyncMetadataApi(client).send(Payload(value="request"))
    finally:
        client.close()
    assert result == Payload(value="response")


@pytest.mark.asyncio
async def test_http_metadata_collision_with_signing_fails_before_transport() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(request)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
        ),
        config=ClientConfig(dependencies=_dependencies()),
    )
    try:
        with pytest.raises(CryptoConfigurationError, match="signing outputs"):
            await SigningCollisionApi(client).send(Payload(value="request"))
    finally:
        await client.aclose()
    assert calls == 0


@pytest.mark.asyncio
async def test_algorithm_may_return_only_declared_typed_outputs() -> None:
    outputs: list[CryptoOutputValue[Any]] = []
    context = HttpCryptoContext(
        "operation",
        "profile",
        "pending",
        CryptoDirection.OUTBOUND,
        CryptoStage.ENCODED,
        1,
    )

    class BadCipher:
        name = "bad-output-test-only"

        def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes | CryptoResult[bytes]:
            return crypto_result(value, crypto_value(KEY_ID, "secret-output"))

    with pytest.raises(PayloadEncryptionError) as captured:
        await encrypt_bytes(b"clear", encrypt_encoded(using=BadCipher()), context=context)
    assert "secret-output" not in str(captured.value)

    class GoodCipher(BadCipher):
        name = "good-output-test-only"

    encrypted = await encrypt_bytes(
        b"clear",
        encrypt_encoded(using=GoodCipher(), outputs=(KEY_ID,)),
        context=context,
        outputs=outputs,
    )
    assert encrypted == b"clear"
    assert outputs == [crypto_value(KEY_ID, "secret-output")]


@pytest.mark.asyncio
async def test_input_lifetime_is_attempt_by_default_and_generation_cached_for_ws() -> None:
    dependency = cast(
        RequestDependency[int], RequestDependency.typed("counter", int, secret=True)
    )
    attempt_input = crypto_input(dependency)
    connection_input = crypto_input(
        dependency, scope=CryptoInputScope.CONNECTION
    )
    calls = 0

    class Provider:
        def resolve(self, context: DependencyContext) -> int:
            nonlocal calls
            calls += 1
            return calls

    registry = DependencyRegistry()
    registry.register(dependency, Provider())

    first, _ = await resolve_crypto_inputs(
        (attempt_input,), registry, operation_id="op", attempt=1
    )
    second, _ = await resolve_crypto_inputs(
        (attempt_input,), registry, operation_id="op", attempt=2
    )
    assert (first.input(attempt_input), second.input(attempt_input)) == (1, 2)

    generation_cache: dict[int, object] = {}
    first_generation, _ = await resolve_crypto_inputs(
        (connection_input,),
        registry,
        operation_id="connection",
        attempt=1,
        cache=generation_cache,
    )
    same_generation, _ = await resolve_crypto_inputs(
        (connection_input,),
        registry,
        operation_id="connection",
        attempt=1,
        cache=generation_cache,
    )
    assert first_generation.input(connection_input) == 3
    assert same_generation.input(connection_input) == 3
    assert calls == 3


class SecretDocument(BaseModel):
    secret: str


@dataclass(frozen=True)
class DocumentCipher:
    name: str = "document-metadata-test-only"

    def encrypt(
        self, value: FrozenValue, *, context: CryptoContext
    ) -> FrozenValue | CryptoResult[FrozenValue]:
        return crypto_result("encrypted:" + str(value), crypto_value(KEY_ID, "doc-key"))

    def decrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert context.metadata(KEY_ID) == "doc-key"
        assert isinstance(value, str)
        return value.removeprefix("encrypted:")


@pytest.mark.asyncio
async def test_websocket_document_metadata_uses_reserved_envelope_field() -> None:
    cipher = DocumentCipher()
    profile = payload_crypto(
        "ws-document-metadata-v1",
        outbound=encrypt_outbound(
            encrypt_field(
                SecretDocument,
                lambda body: body.secret,
                using=cipher,
                outputs=(KEY_ID,),
            )
        ),
        inbound=decrypt_inbound(
            decrypt_field(
                SecretDocument,
                lambda body: body.secret,
                using=cipher,
                metadata=(KEY_ID,),
            )
        ),
    )
    wire = websocket_encrypted(metadata=(websocket_crypto_field(KEY_ID, "crypto", "kid"),))
    compiled = compile_payload_crypto(
        profile,
        default_model_adapters(),
        outbound_model=SecretDocument,
        inbound_models=(SecretDocument,),
    )
    _validate_websocket_crypto(compiled, wire, ())
    with pytest.raises(CryptoConfigurationError, match="reserved writer"):
        _validate_websocket_crypto(
            compiled,
            wire,
            (MessageReservedOutput(object(), ("crypto",)),),
        )
    outputs: list[CryptoOutputValue[Any]] = []
    context = WebSocketCryptoContext(
        "send-secret",
        profile.name,
        "pending",
        CryptoDirection.OUTBOUND,
        CryptoStage.DOCUMENT,
        1,
    )
    protected = await protect_ws_document(
        freeze_value({"secret": "value"}), compiled, context=context, outputs=outputs
    )
    envelope = apply_ws_crypto_metadata(
        freeze_value({"type": "secret", "data": thaw_value(protected)}), wire, outputs
    )
    assert cast(dict[str, object], thaw_value(envelope))["crypto"] == {"kid": "doc-key"}

    message = ProtocolMessage(
        InboundMessageKind.MESSAGE,
        "secret",
        protected,
        envelope=envelope,
    )
    clear = await unprotect_ws_message(
        message,
        compiled,
        wire,
        context=WebSocketCryptoContext(
            "send-secret",
            profile.name,
            "pending",
            CryptoDirection.INBOUND,
            CryptoStage.DOCUMENT,
            1,
        ),
    )
    assert thaw_value(clear.payload) == {"secret": "value"}


def test_websocket_whole_frame_rejects_operation_aad_and_detached_outputs() -> None:
    operation_profile = payload_crypto(
        "ws-operation-aad",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=METADATA_CIPHER)),
        inputs=(TENANT,),
    )
    compiled = compile_payload_crypto(operation_profile, default_model_adapters())
    with pytest.raises(CryptoConfigurationError, match="connection-scoped"):
        _validate_websocket_crypto(compiled, websocket_encrypted(), ())

    output_profile = payload_crypto(
        "ws-detached-output",
        outbound=encrypt_outbound(
            encoded=encrypt_encoded(using=METADATA_CIPHER, outputs=(KEY_ID,))
        ),
        inputs=(CONNECTION_TENANT,),
    )
    compiled = compile_payload_crypto(output_profile, default_model_adapters())
    with pytest.raises(CryptoConfigurationError, match="byte envelope"):
        _validate_websocket_crypto(
            compiled,
            websocket_encrypted(metadata=(websocket_crypto_field(KEY_ID, "kid"),)),
            (MessageReservedOutput(object(), ("auth",)),),
        )
