from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from io import BytesIO
from typing import Annotated, Any, cast

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import AsyncApi, ClientConfig, SyncApi, api
from eazy_sdk.clients import CallOptions, RetryPolicy
from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoContext,
    CryptoRegistry,
    CryptoRule,
    CryptoRuntimeMismatchError,
    CryptoStreamingUnsupportedError,
    EncryptedMediaTypeMismatchError,
    FrozenValue,
    PayloadDecryptionError,
    decrypt_encoded,
    decrypt_field,
    decrypt_inbound,
    encrypt_encoded,
    encrypt_field,
    encrypt_outbound,
    http_crypto_scope,
    http_encrypted,
    payload_crypto,
)
from eazy_sdk.request import (
    BytesBody,
    Header,
    JsonBody,
    ReplayableStreamBody,
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    header_output,
    hmac_sha256,
)
from eazy_sdk.request.prepared import ReplayableBodyStream
from eazy_sdk.response import Empty, Json, ResponseEnvelope, Responses, Success
from tests._support.zapros_clients import client_from_httpx


class Card(BaseModel):
    number: str
    cvv: str


class CreatePayment(BaseModel):
    card: Card
    amount: int


class PaymentResult(BaseModel):
    payment_id: str
    receipt: str


@dataclass(frozen=True)
class FieldCipher:
    name: str = "field-test-only"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert isinstance(value, str)
        return "field:" + value

    def decrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert isinstance(value, str)
        if not value.startswith("field:"):
            raise ValueError("not encrypted")
        return value.removeprefix("field:")


@dataclass
class BodyCipher:
    name: str = "body-test-only"
    encryptions: list[bytes] = field(default_factory=list)

    def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        nonce = len(self.encryptions) + 1
        output = f"enc:{nonce}:".encode() + base64.b64encode(value)
        self.encryptions.append(output)
        return output

    def decrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        prefix, nonce, encoded = value.split(b":", 2)
        assert prefix == b"enc"
        assert nonce
        return base64.b64decode(encoded)


FIELD_CIPHER = FieldCipher()
BODY_CIPHER = BodyCipher()
PAYMENT_CRYPTO = payload_crypto(
    "payments-v1",
    outbound=encrypt_outbound(
        encrypt_field(
            CreatePayment,
            lambda body: body.card.number,
            using=FIELD_CIPHER,
        ),
        encrypt_field(
            CreatePayment,
            lambda body: body.card.cvv,
            using=FIELD_CIPHER,
        ),
        encoded=encrypt_encoded(using=BODY_CIPHER),
    ),
    inbound=decrypt_inbound(
        decrypt_field(
            PaymentResult,
            lambda body: body.receipt,
            using=FIELD_CIPHER,
        ),
        encoded=decrypt_encoded(using=BODY_CIPHER),
    ),
)
WIRE = http_encrypted(
    content_type="application/vnd.example.encrypted+json",
    clear_content_type="application/json",
    plaintext_statuses=frozenset({503}),
)
RESPONSES = Responses[PaymentResult](success={200: Json(PaymentResult)})


class AsyncPaymentsApi(AsyncApi):
    @api.post(
        "/payments",
        responses=RESPONSES,
        crypto=PAYMENT_CRYPTO,
        crypto_wire=WIRE,
        idempotent=True,
    )
    async def create(
        self,
        *,
        request: Annotated[CreatePayment, JsonBody()],
    ) -> PaymentResult:
        raise AssertionError("declaration body must not execute")


class SyncPaymentsApi(SyncApi):
    @api.post(
        "/payments",
        responses=RESPONSES,
        crypto=PAYMENT_CRYPTO,
        crypto_wire=WIRE,
    )
    def create(
        self,
        *,
        request: Annotated[CreatePayment, JsonBody()],
    ) -> PaymentResult:
        raise AssertionError("declaration body must not execute")


def _clear_request(content: bytes) -> dict[str, Any]:
    _, _, encoded = content.split(b":", 2)
    return cast(dict[str, Any], json.loads(base64.b64decode(encoded)))


def _encrypted_response() -> bytes:
    clear = json.dumps(
        {"payment_id": "pay-1", "receipt": "field:receipt-1"},
        separators=(",", ":"),
    ).encode()
    return b"enc:server:" + base64.b64encode(clear)


async def test_async_http_field_and_encoded_crypto_wraps_codec_and_model_validation() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.headers["content-type"] == WIRE.content_type
        assert int(request.headers["content-length"]) == len(request.content)
        assert _clear_request(request.content) == {
            "card": {"number": "field:4111", "cvv": "field:123"},
            "amount": 500,
        }
        return httpx.Response(
            200,
            content=_encrypted_response(),
            headers={"Content-Type": WIRE.content_type},
        )

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    envelope = await AsyncPaymentsApi(client).create.with_response(
        request=CreatePayment(card=Card(number="4111", cvv="123"), amount=500)
    )
    await client.aclose()

    assert isinstance(envelope, ResponseEnvelope)
    assert envelope.value == PaymentResult(payment_id="pay-1", receipt="receipt-1")
    assert envelope.response.content_type == "application/json"
    assert envelope.headers["content-type"] == WIRE.content_type
    assert envelope.response.wire_body == _encrypted_response()
    assert b"receipt-1" in envelope.response.body
    assert "receipt-1" not in repr(envelope.response)
    assert len(captured) == 1


def test_sync_http_uses_the_same_crypto_profile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert _clear_request(request.content)["card"]["number"] == "field:5555"
        return httpx.Response(
            200,
            content=_encrypted_response(),
            headers={"Content-Type": WIRE.content_type},
        )

    client = client_from_httpx(
        httpx.Client(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    result = SyncPaymentsApi(client).create(
        request=CreatePayment(card=Card(number="5555", cvv="999"), amount=100)
    )
    client.close()
    assert result.receipt == "receipt-1"


async def test_field_only_crypto_preserves_json_media_type() -> None:
    field_profile = payload_crypto(
        "fields-only-v1",
        outbound=encrypt_outbound(
            encrypt_field(CreatePayment, lambda body: body.card.number, using=FIELD_CIPHER)
        ),
        inbound=decrypt_inbound(
            decrypt_field(PaymentResult, lambda body: body.receipt, using=FIELD_CIPHER)
        ),
    )

    class FieldOnlyApi(AsyncApi):
        @api.post("/field-only", responses=RESPONSES, crypto=field_profile)
        async def create(
            self,
            *,
            request: Annotated[CreatePayment, JsonBody()],
        ) -> PaymentResult:
            raise AssertionError

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content)["card"]["number"] == "field:4111"
        return httpx.Response(
            200,
            json={"payment_id": "pay-field", "receipt": "field:receipt-field"},
        )

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    try:
        result = await FieldOnlyApi(client).create(
            request=CreatePayment(card=Card(number="4111", cvv="123"), amount=500)
        )
    finally:
        await client.aclose()

    assert result.receipt == "receipt-field"


async def test_whole_payload_crypto_uses_octet_stream_wire_default() -> None:
    class DefaultWireApi(AsyncApi):
        @api.post("/default-wire", responses=RESPONSES, crypto=PAYMENT_CRYPTO)
        async def create(
            self,
            *,
            request: Annotated[CreatePayment, JsonBody()],
        ) -> PaymentResult:
            raise AssertionError

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/octet-stream"
        return httpx.Response(
            200,
            content=_encrypted_response(),
            headers={"Content-Type": "application/octet-stream"},
        )

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    try:
        result = await DefaultWireApi(client).create(
            request=CreatePayment(card=Card(number="4111", cvv="123"), amount=500)
        )
    finally:
        await client.aclose()

    assert result.receipt == "receipt-1"


async def test_retry_runs_outbound_crypto_again_with_fresh_algorithm_state() -> None:
    requests: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.content)
        if len(requests) == 1:
            return httpx.Response(
                503,
                json={"error": "retry"},
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(
            200,
            content=_encrypted_response(),
            headers={"Content-Type": WIRE.content_type},
        )

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            retry=RetryPolicy.safe(
                max_attempts=2,
                retry_statuses=frozenset({503}),
            )
        ),
    )
    result = await AsyncPaymentsApi(client).create(
        request=CreatePayment(card=Card(number="4111", cvv="123"), amount=500)
    )
    await client.aclose()

    assert result.payment_id == "pay-1"
    assert len(requests) == 2
    assert requests[0] != requests[1]
    assert _clear_request(requests[0]) == _clear_request(requests[1])


def test_sync_runtime_rejects_async_algorithm_before_network() -> None:
    class AsyncBodyCipher:
        name = "async-body-test-only"

        async def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
            return value

    invalid = payload_crypto(
        "async-only",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=AsyncBodyCipher())),
    )

    class InvalidApi(SyncApi):
        @api.post(
            "/invalid",
            responses=RESPONSES,
            crypto=invalid,
        )
        def create(
            self,
            *,
            request: Annotated[CreatePayment, JsonBody()],
        ) -> PaymentResult:
            raise AssertionError

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = client_from_httpx(
        httpx.Client(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    with pytest.raises(CryptoRuntimeMismatchError):
        InvalidApi(client).create(
            request=CreatePayment(card=Card(number="1", cvv="2"), amount=1)
        )
    client.close()
    assert calls == 0


async def test_client_scope_applies_and_explicit_none_disables_inheritance() -> None:
    class ScopedApi(AsyncApi):
        @api.post("/scoped", responses=RESPONSES)
        async def encrypted(
            self,
            *,
            request: Annotated[CreatePayment, JsonBody()],
        ) -> PaymentResult:
            raise AssertionError

        @api.post("/plain", responses=RESPONSES, crypto=None)
        async def plain(
            self,
            *,
            request: Annotated[CreatePayment, JsonBody()],
        ) -> PaymentResult:
            raise AssertionError

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/scoped":
            assert request.headers["content-type"] == WIRE.content_type
            return httpx.Response(
                200,
                content=_encrypted_response(),
                headers={"Content-Type": WIRE.content_type},
            )
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content)["card"]["number"] == "4111"
        return httpx.Response(
            200,
            json={"payment_id": "pay-plain", "receipt": "clear"},
        )

    registry = CryptoRegistry(
        (
            CryptoRule(
                PAYMENT_CRYPTO,
                http_crypto_scope(
                    hosts=("api.example",),
                    path_prefixes=("/",),
                ),
                wire=WIRE,
            ),
        )
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(crypto=registry),
    )
    request = CreatePayment(card=Card(number="4111", cvv="123"), amount=500)
    encrypted = await ScopedApi(client).encrypted(request=request)
    plain = await ScopedApi(client).plain(request=request)
    await client.aclose()
    assert encrypted.receipt == "receipt-1"
    assert plain.payment_id == "pay-plain"


async def test_crypto_wire_rejects_manual_representation_header_before_network() -> None:
    class ConflictingApi(AsyncApi):
        @api.post(
            "/conflict",
            responses=RESPONSES,
            crypto=PAYMENT_CRYPTO,
            crypto_wire=WIRE,
        )
        async def create(
            self,
            *,
            request: Annotated[CreatePayment, JsonBody()],
            content_type: Annotated[str, Header("Content-Type")],
        ) -> PaymentResult:
            raise AssertionError

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    with pytest.raises(CryptoConfigurationError, match="owns HTTP representation headers"):
        await ConflictingApi(client).create(
            request=CreatePayment(card=Card(number="1", cvv="2"), amount=1),
            content_type="application/json",
        )
    await client.aclose()
    assert calls == 0


async def test_exact_signature_reads_the_ciphertext_that_reaches_handler() -> None:
    key_requirement = SigningKeyRequirement("crypto-signing-test")
    signature = hmac_sha256(
        key=key_requirement,
        base=body_digest(),
        output=header_output("X-Signature"),
    )

    class SignedApi(AsyncApi):
        @api.post(
            "/signed",
            responses=RESPONSES,
            crypto=PAYMENT_CRYPTO,
            crypto_wire=WIRE,
            signing=signature,
        )
        async def create(
            self,
            *,
            request: Annotated[CreatePayment, JsonBody()],
        ) -> PaymentResult:
            raise AssertionError

    async def handler(request: httpx.Request) -> httpx.Response:
        body_hash = hashlib.sha256(request.content).hexdigest().encode()
        expected = hmac.new(b"signing-secret", body_hash, hashlib.sha256).hexdigest()
        assert request.headers["x-signature"] == expected
        return httpx.Response(
            200,
            content=_encrypted_response(),
            headers={"Content-Type": WIRE.content_type},
        )

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            key_provider=lambda requirement: (
                SigningKey(b"signing-secret")
                if requirement is key_requirement
                else SigningKey(b"wrong")
            )
        ),
    )
    result = await SignedApi(client).create(
        request=CreatePayment(card=Card(number="4111", cvv="123"), amount=500)
    )
    await client.aclose()
    assert result.payment_id == "pay-1"


@pytest.mark.parametrize(
    ("content_type", "content", "error_type"),
    [
        ("application/json", b'{"payment_id":"clear"}', EncryptedMediaTypeMismatchError),
        (
            WIRE.content_type,
            b"ciphertext=customer-secret key=do-not-expose",
            PayloadDecryptionError,
        ),
    ],
)
async def test_inbound_crypto_fails_closed_without_leaking_wire_data(
    content_type: str,
    content: bytes,
    error_type: type[Exception],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"Content-Type": content_type})

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    try:
        with pytest.raises(error_type) as captured:
            await AsyncPaymentsApi(client).create(
                request=CreatePayment(card=Card(number="4111", cvv="123"), amount=500)
            )
    finally:
        await client.aclose()

    rendered = repr(captured.value)
    assert "customer-secret" not in rendered
    assert "do-not-expose" not in rendered
    assert captured.value.__cause__ is None


async def test_redirect_re_resolves_crypto_scope_and_rebuilds_from_logical_body() -> None:
    redirect_wire = http_encrypted(
        content_type=WIRE.content_type,
        clear_content_type=WIRE.clear_content_type,
        plaintext_statuses=frozenset((*WIRE.plaintext_statuses, 307)),
    )

    class ScopedRedirectApi(AsyncApi):
        @api.post("/payments", responses=RESPONSES)
        async def create(
            self,
            *,
            request: Annotated[CreatePayment, JsonBody()],
            options: CallOptions | None = None,
        ) -> PaymentResult:
            raise AssertionError

    seen: list[tuple[str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.content))
        if request.url.host == "api.example":
            assert request.headers["content-type"] == WIRE.content_type
            return httpx.Response(
                307,
                headers={"Location": "https://clear.example/payments"},
            )
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content)["card"]["number"] == "4111"
        return httpx.Response(
            200,
            json={"payment_id": "pay-clear", "receipt": "clear"},
        )

    registry = CryptoRegistry(
        (
            CryptoRule(
                PAYMENT_CRYPTO,
                http_crypto_scope(hosts=("api.example",)),
                wire=redirect_wire,
            ),
        )
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(crypto=registry),
    )
    try:
        result = await ScopedRedirectApi(client).create(
            request=CreatePayment(card=Card(number="4111", cvv="123"), amount=500),
            options=CallOptions(max_attempts=2, max_redirects=1),
        )
    finally:
        await client.aclose()

    assert result.payment_id == "pay-clear"
    assert seen[0][1] != seen[1][1]


async def test_content_coding_runs_before_whole_payload_encryption() -> None:
    cipher = BodyCipher()
    profile = payload_crypto(
        "compressed-v1",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=cipher)),
    )
    responses: Responses[None] = Responses(success=(Success(204, Empty()),))

    class CompressedApi(AsyncApi):
        @api.post(
            "/compressed",
            responses=responses,
            crypto=profile,
            crypto_wire=WIRE,
        )
        async def send(
            self,
            *,
            body: Annotated[bytes, BytesBody(content_encoding="gzip")],
        ) -> None:
            raise AssertionError

    async def handler(request: httpx.Request) -> httpx.Response:
        encrypted = request.content
        clear_compressed = base64.b64decode(encrypted.split(b":", 2)[2])
        assert gzip.decompress(clear_compressed) == b"compress me"
        assert request.headers["content-type"] == WIRE.content_type
        return httpx.Response(204)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    try:
        await CompressedApi(client).send(body=b"compress me")
    finally:
        await client.aclose()


async def test_whole_payload_crypto_rejects_stream_without_buffering_or_network() -> None:
    stream_profile = payload_crypto(
        "stream-v1",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=BodyCipher())),
    )

    class StreamApi(AsyncApi):
        @api.post("/stream", responses=RESPONSES, crypto=stream_profile)
        async def upload(
            self,
            *,
            body: Annotated[ReplayableBodyStream, ReplayableStreamBody()],
        ) -> PaymentResult:
            raise AssertionError

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    try:
        with pytest.raises(CryptoStreamingUnsupportedError):
            await StreamApi(client).upload(
                body=ReplayableBodyStream(
                    lambda: BytesIO(b"secret-stream"), known_length=13
                )
            )
    finally:
        await client.aclose()

    assert calls == 0
