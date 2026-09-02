from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Annotated, Any, NotRequired, TypedDict, Unpack, cast

import httpx
import pytest

from eazy_sdk import ApiDefaults, AsyncApi, AsyncClient, ClientConfig, api
from eazy_sdk._internal import PlanError, WriterConflictError
from eazy_sdk.accounts.session import SessionKey, SessionRevision
from eazy_sdk.auth import Auth, BearerScheme
from eazy_sdk.auth.core import (
    AuthExecution,
    AuthProviderIdentity,
    AuthProviders,
    ResolvedAuth,
)
from eazy_sdk.clients import RetryPolicy
from eazy_sdk.codecs import EncodeContext
from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoContext,
    FrozenValue,
    encrypt_encoded,
    encrypt_field,
    encrypt_outbound,
    http_encrypted,
    payload_crypto,
)
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.protection.advanced import FromProtection, ProtectionRequirement
from eazy_sdk.request import (
    BodyProjection,
    JsonBody,
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    body_output,
    header_output,
    hmac_sha256,
    method,
)
from eazy_sdk.response import Empty, Responses, Success


class PaymentSource(TypedDict):
    card_number: str
    amount: int


class CardWire(TypedDict):
    number: str


class PaymentWire(TypedDict):
    card: CardWire
    amount: int


def payment_to_wire(source: PaymentSource) -> PaymentWire:
    return {
        "card": {"number": source["card_number"]},
        "amount": source["amount"],
    }


NO_CONTENT: Responses[None] = Responses(success=(Success(204, Empty()),))
SIGNING_KEY = SigningKeyRequirement("phase21-signing")
SIGNATURE_PROTECTION = ProtectionRequirement[dict[str, str]]("signature-source")


@dataclass
class AttemptFieldCipher:
    attempts: list[int] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "phase21-field-test-only"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert isinstance(value, str)
        self.attempts.append(context.attempt)
        return f"attempt-{context.attempt}:{value}"


async def test_projection_target_crypto_and_exact_signature_are_fresh_on_retry() -> None:
    cipher = AttemptFieldCipher()
    crypto = payload_crypto(
        "phase21-fields",
        outbound=encrypt_outbound(
            encrypt_field(
                PaymentWire,
                lambda body: body["card"]["number"],
                using=cipher,
            )
        ),
    )
    signature = hmac_sha256(
        key=SIGNING_KEY,
        base=body_digest(),
        output=header_output("X-Body-Signature"),
        name="phase21-exact",
    )
    projection = BodyProjection(
        PaymentSource,
        PaymentWire,
        payment_to_wire,
        JsonBody(),
    )

    class PaymentApi(AsyncApi):
        @api.put(
            "/payments",
            responses=NO_CONTENT,
            body=projection,
            crypto=crypto,
            signing=signature,
        )
        async def pay(self, **request: Unpack[PaymentSource]) -> None:
            raise NotImplementedError

    captures: list[tuple[bytes, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        signature_value = request.headers["x-body-signature"]
        expected = hmac.new(
            b"secret",
            hashlib.sha256(request.content).hexdigest().encode(),
            hashlib.sha256,
        ).hexdigest()
        assert signature_value == expected
        captures.append((request.content, signature_value))
        if len(captures) == 1:
            return httpx.Response(503)
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(
            retry=RetryPolicy.safe(max_attempts=2),
            key_provider=lambda _requirement: SigningKey(b"secret"),
        ),
    ) as client:
        await PaymentApi(client).pay(card_number="4111", amount=50)

    assert cipher.attempts == [1, 2]
    assert json.loads(captures[0][0])["card"]["number"] == "attempt-1:4111"
    assert json.loads(captures[1][0])["card"]["number"] == "attempt-2:4111"
    assert captures[0] != captures[1]


async def test_projection_crypto_and_signature_are_fresh_on_managed_redirect() -> None:
    cipher = AttemptFieldCipher()
    projections: list[int] = []

    def project(source: PaymentSource) -> PaymentWire:
        projections.append(len(projections) + 1)
        return payment_to_wire(source)

    projection = BodyProjection(PaymentSource, PaymentWire, project, JsonBody())
    crypto = payload_crypto(
        "phase21-redirect-fields",
        outbound=encrypt_outbound(
            encrypt_field(
                PaymentWire,
                lambda body: body["card"]["number"],
                using=cipher,
            )
        ),
    )
    signature = hmac_sha256(
        key=SIGNING_KEY,
        base=body_digest(),
        output=header_output("X-Body-Signature"),
    )

    class RedirectApi(AsyncApi):
        @api.put(
            "/redirect",
            responses=NO_CONTENT,
            body=projection,
            crypto=crypto,
            signing=signature,
        )
        async def send(self, **request: Unpack[PaymentSource]) -> None:
            raise NotImplementedError

    captures: list[tuple[str, bytes, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captures.append(
            (request.url.path, request.content, request.headers["x-body-signature"])
        )
        if request.url.path == "/redirect":
            return httpx.Response(307, headers={"Location": "/redirected"})
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(
            max_redirects=1,
            key_provider=lambda _requirement: SigningKey(b"secret"),
        ),
    ) as client:
        await RedirectApi(client).send(card_number="4111", amount=50)

    assert projections == [1, 2]
    assert cipher.attempts == [1, 2]
    assert [item[0] for item in captures] == ["/redirect", "/redirected"]
    assert captures[0][1:] != captures[1][1:]


async def test_projection_crypto_and_signature_are_fresh_on_auth_replay() -> None:
    scheme = BearerScheme("phase21-refresh")

    @dataclass
    class RefreshingProvider:
        token: str = "access-v1"
        revision: int = 1
        can_refresh: bool = True

        async def resolve(self) -> ResolvedAuth[str]:
            return ResolvedAuth(
                self.token,
                AuthExecution(
                    scheme,
                    AuthProviderIdentity("phase21-refresh-provider"),
                    SessionKey("phase21-refresh-session"),
                    SessionRevision(self.revision),
                    -1,
                ),
            )

        async def refresh_execution(
            self,
            _execution: AuthExecution[str],
            _graph: object,
        ) -> ResolvedAuth[str]:
            self.token = "access-v2"
            self.revision = 2
            return await self.resolve()

    provider = RefreshingProvider()
    providers = AuthProviders()
    providers.register(scheme, provider)
    auth = Auth._bind(scheme, providers)
    cipher = AttemptFieldCipher()
    projections: list[int] = []

    def project(source: PaymentSource) -> PaymentWire:
        projections.append(len(projections) + 1)
        return payment_to_wire(source)

    projection = BodyProjection(PaymentSource, PaymentWire, project, JsonBody())
    crypto = payload_crypto(
        "phase21-auth-fields",
        outbound=encrypt_outbound(
            encrypt_field(
                PaymentWire,
                lambda body: body["card"]["number"],
                using=cipher,
            )
        ),
    )
    signature = hmac_sha256(
        key=SIGNING_KEY,
        base=body_digest(),
        output=header_output("X-Body-Signature"),
    )

    class AuthApi(AsyncApi):
        @api.put(
            "/auth-replay",
            responses=NO_CONTENT,
            body=projection,
            crypto=crypto,
            signing=signature,
            security=scheme,
        )
        async def send(self, **request: Unpack[PaymentSource]) -> None:
            raise NotImplementedError

    captures: list[tuple[str, bytes, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captures.append(
            (
                request.headers["authorization"],
                request.content,
                request.headers["x-body-signature"],
            )
        )
        if len(captures) == 1:
            return httpx.Response(
                401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(
            auth=auth,
            auth_retries=1,
            key_provider=lambda _requirement: SigningKey(b"secret"),
        ),
    ) as client:
        await AuthApi(client).send(card_number="4111", amount=50)

    assert projections == [1, 2]
    assert cipher.attempts == [1, 2]
    assert [item[0] for item in captures] == ["Bearer access-v1", "Bearer access-v2"]
    assert captures[0][1:] != captures[1][1:]


@dataclass
class GzipJsonCodec:
    documents: list[object] = field(default_factory=list)
    name: str = "phase21-gzip-json"
    media_type: str = "application/json"

    def encode(self, value: object, context: EncodeContext) -> bytes:
        assert context.operation_id == "compressedProjection"
        self.documents.append(value)
        return gzip.compress(
            json.dumps(value, separators=(",", ":")).encode(),
            mtime=0,
        )


@dataclass
class EncodedCipher:
    clear_inputs: list[bytes] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "phase21-encoded-test-only"

    def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        self.clear_inputs.append(value)
        return b"enc:" + base64.b64encode(value)


async def test_custom_compression_precedes_encoded_crypto_and_exact_signing() -> None:
    codec = GzipJsonCodec()
    cipher = EncodedCipher()
    projection = BodyProjection(PaymentSource, PaymentWire, payment_to_wire, codec)
    crypto = payload_crypto(
        "phase21-encoded",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=cipher)),
    )
    wire = http_encrypted(content_type="application/vnd.phase21.encrypted")
    signature = hmac_sha256(
        key=SIGNING_KEY,
        base=body_digest(),
        output=header_output("X-Ciphertext-Signature"),
    )

    class CompressedApi(AsyncApi):
        @api.post(
            "/compressed",
            operation_id="compressedProjection",
            responses=NO_CONTENT,
            body=projection,
            crypto=crypto,
            crypto_wire=wire,
            signing=signature,
        )
        async def send(self, **request: Unpack[PaymentSource]) -> None:
            raise NotImplementedError

    async def handler(request: httpx.Request) -> httpx.Response:
        expected = hmac.new(
            b"secret",
            hashlib.sha256(request.content).hexdigest().encode(),
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["x-ciphertext-signature"] == expected
        assert request.headers["content-type"] == wire.content_type
        compressed = base64.b64decode(request.content.removeprefix(b"enc:"))
        assert json.loads(gzip.decompress(compressed)) == {
            "card": {"number": "5555"},
            "amount": 90,
        }
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(key_provider=lambda _requirement: SigningKey(b"secret")),
    ) as client:
        await CompressedApi(client).send(card_number="5555", amount=90)

    assert codec.documents == [{"card": {"number": "5555"}, "amount": 90}]
    assert len(cipher.clear_inputs) == 1
    assert cipher.clear_inputs[0].startswith(b"\x1f\x8b")


class SignatureFields(TypedDict, total=False):
    value: NotRequired[str]


class SignedWire(TypedDict):
    payload: str
    signatures: SignatureFields


class SignedSource(TypedDict):
    payload: str


class ManagedSignatureFields(TypedDict, total=False):
    value: NotRequired[
        Annotated[str, FromProtection(SIGNATURE_PROTECTION, "value")]
    ]


class ManagedSignedWire(TypedDict):
    payload: str
    signatures: ManagedSignatureFields


def signed_to_wire(source: SignedSource) -> SignedWire:
    return {"payload": source["payload"], "signatures": {}}


async def test_nested_body_reserved_output_uses_the_projected_target_path() -> None:
    output = body_output("value", json_pointer="/signatures/value", position=0)
    signature = hmac_sha256(
        key=SIGNING_KEY,
        base=method(),
        output=output,
        name="embedded",
    )
    projection = BodyProjection(SignedSource, SignedWire, signed_to_wire, JsonBody())

    class SignedApi(AsyncApi):
        @api.post(
            "/signed",
            responses=NO_CONTENT,
            body=projection,
            signing=signature,
        )
        async def send(self, **request: Unpack[SignedSource]) -> None:
            raise NotImplementedError

    expected = hmac.new(b"secret", b"POST", hashlib.sha256).hexdigest()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "payload": "visible",
            "signatures": {"value": expected},
        }
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(key_provider=lambda _requirement: SigningKey(b"secret")),
    ) as client:
        await SignedApi(client).send(payload="visible")

    descriptor = cast(Any, SignedApi.send)
    compiled = descriptor.resolve(ApiDefaults()).compile()
    assert compiled.body_signature_paths == (("signatures", "value"),)


def test_private_and_signature_writer_collision_fails_during_compile() -> None:
    def project(source: SignedSource) -> ManagedSignedWire:
        return {"payload": source["payload"], "signatures": {}}

    projection = BodyProjection(
        SignedSource,
        ManagedSignedWire,
        project,
        JsonBody(),
    )
    signature = hmac_sha256(
        key=SIGNING_KEY,
        base=method(),
        output=body_output("value", json_pointer="/signatures/value"),
    )

    class CollisionApi(AsyncApi):
        @api.post(
            "/collision",
            responses=NO_CONTENT,
            body=projection,
            protections=(SIGNATURE_PROTECTION,),
            signing=signature,
        )
        async def send(self, **request: Unpack[SignedSource]) -> None:
            raise NotImplementedError

    descriptor = cast(Any, CollisionApi.send)
    with pytest.raises(PlanError, match="private wire and body signature writers overlap"):
        descriptor.resolve(ApiDefaults()).compile()


def test_body_signature_output_must_select_a_declared_target_field() -> None:
    projection = BodyProjection(SignedSource, SignedWire, signed_to_wire, JsonBody())
    signature = hmac_sha256(
        key=SIGNING_KEY,
        base=method(),
        output=body_output("missing", json_pointer="/signatures/missing"),
    )

    class InvalidTargetApi(AsyncApi):
        @api.post(
            "/invalid-target",
            responses=NO_CONTENT,
            body=projection,
            signing=signature,
        )
        async def send(self, **request: Unpack[SignedSource]) -> None:
            raise NotImplementedError

    descriptor = cast(Any, InvalidTargetApi.send)
    with pytest.raises(PlanError, match="target field is not declared"):
        descriptor.resolve(ApiDefaults()).compile()


async def test_body_signature_output_and_encoded_crypto_fail_before_key_or_network() -> None:
    output = body_output("value", json_pointer="/signatures/value")
    signature = hmac_sha256(key=SIGNING_KEY, base=method(), output=output)
    cipher = EncodedCipher()
    crypto = payload_crypto(
        "invalid-body-output",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=cipher)),
    )
    projection = BodyProjection(SignedSource, SignedWire, signed_to_wire, JsonBody())

    class InvalidApi(AsyncApi):
        @api.post(
            "/invalid",
            responses=NO_CONTENT,
            body=projection,
            crypto=crypto,
            signing=signature,
        )
        async def send(self, **request: Unpack[SignedSource]) -> None:
            raise NotImplementedError

    keys = 0
    sends = 0

    def key_provider(_requirement: SigningKeyRequirement) -> SigningKey:
        nonlocal keys
        keys += 1
        return SigningKey(b"secret")

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(key_provider=key_provider),
    ) as client:
        with pytest.raises(
            CryptoConfigurationError,
            match="body signature outputs cannot run after outbound encoded crypto",
        ):
            await InvalidApi(client).send(payload="visible")

    assert keys == 0
    assert sends == 0
    assert cipher.clear_inputs == []


async def test_body_signature_and_field_crypto_writer_collision_fails_before_side_effects() -> None:
    output = body_output("value", json_pointer="/signatures/value")
    signature = hmac_sha256(key=SIGNING_KEY, base=method(), output=output)
    crypto = payload_crypto(
        "invalid-field-writer",
        outbound=encrypt_outbound(
            encrypt_field(
                SignedWire,
                lambda body: body["signatures"]["value"],
                using=AttemptFieldCipher(),
            )
        ),
    )
    projection = BodyProjection(SignedSource, SignedWire, signed_to_wire, JsonBody())

    class InvalidApi(AsyncApi):
        @api.post(
            "/invalid-field",
            responses=NO_CONTENT,
            body=projection,
            crypto=crypto,
            signing=signature,
        )
        async def send(self, **request: Unpack[SignedSource]) -> None:
            raise NotImplementedError

    keys = 0
    sends = 0

    def key_provider(_requirement: SigningKeyRequirement) -> SigningKey:
        nonlocal keys
        keys += 1
        return SigningKey(b"secret")

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(key_provider=key_provider),
    ) as client:
        with pytest.raises(
            CryptoConfigurationError,
            match="body signature and outbound crypto writers overlap",
        ):
            await InvalidApi(client).send(payload="visible")

    assert keys == 0
    assert sends == 0


async def test_document_crypto_is_bound_to_projection_target_not_public_source() -> None:
    invalid_crypto = payload_crypto(
        "public-source-is-not-wire",
        outbound=encrypt_outbound(
            encrypt_field(
                PaymentSource,
                lambda source: source["card_number"],
                using=AttemptFieldCipher(),
            )
        ),
    )
    projection = BodyProjection(
        PaymentSource,
        PaymentWire,
        payment_to_wire,
        JsonBody(),
    )

    class InvalidModelApi(AsyncApi):
        @api.put(
            "/invalid-model",
            responses=NO_CONTENT,
            body=projection,
            crypto=invalid_crypto,
        )
        async def send(self, **request: Unpack[PaymentSource]) -> None:
            raise NotImplementedError

    sends = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
    ) as client:
        with pytest.raises(
            CryptoConfigurationError,
            match="outbound crypto model PaymentSource does not match operation model PaymentWire",
        ):
            await InvalidModelApi(client).send(card_number="4111", amount=50)

    assert sends == 0


async def test_projection_rejects_prepopulated_nested_signature_output() -> None:
    def occupied(source: SignedSource) -> SignedWire:
        return {
            "payload": source["payload"],
            "signatures": {"value": "mapper-owned"},
        }

    projection = BodyProjection(SignedSource, SignedWire, occupied, JsonBody())
    signature = hmac_sha256(
        key=SIGNING_KEY,
        base=method(),
        output=body_output("value", json_pointer="/signatures/value"),
    )

    class OccupiedApi(AsyncApi):
        @api.post(
            "/occupied",
            responses=NO_CONTENT,
            body=projection,
            signing=signature,
        )
        async def send(self, **request: Unpack[SignedSource]) -> None:
            raise NotImplementedError

    sends = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return httpx.Response(204)

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=ClientConfig(key_provider=lambda _requirement: SigningKey(b"secret")),
    ) as client:
        with pytest.raises(WriterConflictError, match=r"signatures\.value"):
            await OccupiedApi(client).send(payload="visible")

    assert sends == 0
