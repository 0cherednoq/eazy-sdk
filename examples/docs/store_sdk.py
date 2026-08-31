"""A local store payment flow: Bearer auth, HMAC and payload crypto.

The ciphers are deliberately reversible Base64 wrappers. They demonstrate where an
application-owned cipher plugs into Eazy SDK; they are not suitable for production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Annotated, TypedDict, Unpack, cast

import httpx
from pydantic import BaseModel

from eazy_sdk import Client, ClientConfig, SyncApi, api
from eazy_sdk.auth import BearerScheme
from eazy_sdk.crypto import (
    CryptoContext,
    CryptoDirection,
    CryptoStage,
    FrozenValue,
    decrypt_encoded,
    decrypt_field,
    decrypt_inbound,
    encrypt_encoded,
    encrypt_field,
    encrypt_outbound,
    http_encrypted,
    payload_crypto,
)
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import (
    JsonBody,
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    header_output,
    hmac_sha256,
)
from eazy_sdk.response import Json, Responses, Success


class Card(BaseModel):
    number: str
    cvv: str


class CreatePayment(BaseModel):
    order_id: str
    card: Card
    amount: int


class PaymentResult(BaseModel):
    id: str
    status: str
    receipt: str


@dataclass(frozen=True, slots=True)
class DemoFieldCipher:
    name: str = "demo-fields-v1"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert isinstance(value, str)
        return "demo:" + base64.b64encode(value.encode()).decode()

    def decrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert isinstance(value, str) and value.startswith("demo:")
        return base64.b64decode(value.removeprefix("demo:")).decode()


@dataclass(frozen=True, slots=True)
class DemoBodyCipher:
    name: str = "demo-body-v1"

    def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        return b"demo-body:" + base64.b64encode(value)

    def decrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        if not value.startswith(b"demo-body:"):
            raise ValueError("unexpected demo ciphertext")
        return base64.b64decode(value.removeprefix(b"demo-body:"))


FIELD_CIPHER = DemoFieldCipher()
BODY_CIPHER = DemoBodyCipher()
PAYMENT_CRYPTO = payload_crypto(
    "store-payments-v1",
    outbound=encrypt_outbound(
        encrypt_field(CreatePayment, lambda body: body.card.number, using=FIELD_CIPHER),
        encrypt_field(CreatePayment, lambda body: body.card.cvv, using=FIELD_CIPHER),
        encoded=encrypt_encoded(using=BODY_CIPHER, max_output_bytes=96_000),
    ),
    inbound=decrypt_inbound(
        decrypt_field(PaymentResult, lambda body: body.receipt, using=FIELD_CIPHER),
        encoded=decrypt_encoded(using=BODY_CIPHER, max_input_bytes=96_000),
    ),
)
PAYMENT_WIRE = http_encrypted(
    content_type="application/vnd.store.encrypted+json",
    clear_content_type="application/json",
)

CUSTOMER = BearerScheme("store-customer")
PAYMENTS_KEY = SigningKeyRequirement("store-payments")
PAYMENTS_HMAC = hmac_sha256(
    key=PAYMENTS_KEY,
    base=body_digest("sha256"),
    output=header_output("X-Signature"),
)
PAYMENT_RESPONSES: Responses[PaymentResult] = Responses(
    success=(Success(201, Json(PaymentResult)),)
)


class CreatePaymentRequest(TypedDict):
    body: Annotated[CreatePayment, JsonBody()]


class PaymentsApi(SyncApi):
    @api.post(
        "/v1/payments",
        operation_id="createPayment",
        responses=PAYMENT_RESPONSES,
        security=CUSTOMER,
        signing=PAYMENTS_HMAC,
        crypto=PAYMENT_CRYPTO,
        crypto_wire=PAYMENT_WIRE,
    )
    def create(self, **request: Unpack[CreatePaymentRequest]) -> PaymentResult:
        raise NotImplementedError


def _decode_request(request: httpx.Request) -> dict[str, object]:
    context = CryptoContext(
        operation_id="demo-server",
        profile="store-payments-v1",
        algorithm=BODY_CIPHER.name,
        direction=CryptoDirection.INBOUND,
        stage=CryptoStage.ENCODED,
        attempt=1,
    )
    clear = BODY_CIPHER.decrypt(request.content, context=context)
    return cast(dict[str, object], json.loads(clear))


def _encode_response(value: dict[str, object]) -> bytes:
    clear = json.dumps(value, separators=(",", ":")).encode()
    context = CryptoContext(
        operation_id="demo-server",
        profile="store-payments-v1",
        algorithm=BODY_CIPHER.name,
        direction=CryptoDirection.OUTBOUND,
        stage=CryptoStage.ENCODED,
        attempt=1,
    )
    return BODY_CIPHER.encrypt(clear, context=context)


def store(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer public-demo-token"
    assert request.headers["Content-Type"] == PAYMENT_WIRE.content_type

    digest = hashlib.sha256(request.content).hexdigest().encode()
    expected = hmac.new(b"local-demo-secret", digest, hashlib.sha256).hexdigest()
    assert request.headers["X-Signature"] == expected

    body = _decode_request(request)
    assert body == {
        "order_id": "order-42",
        "card": {
            "number": "demo:NDExMTExMTExMTExMTExMQ==",
            "cvv": "demo:MTIz",
        },
        "amount": 12_900,
    }
    return httpx.Response(
        201,
        content=_encode_response(
            {
                "id": "pay-42",
                "status": "accepted",
                "receipt": "demo:cmVjZWlwdC1wYXktNDI=",
            }
        ),
        headers={"Content-Type": PAYMENT_WIRE.content_type},
    )


def signing_key(requirement: SigningKeyRequirement) -> SigningKey:
    if requirement == PAYMENTS_KEY:
        return SigningKey(b"local-demo-secret")
    raise LookupError(requirement.name)


def main() -> None:
    raw_client = httpx.Client(
        transport=httpx.MockTransport(store),
        headers={},
        cookies={},
    )
    config = ClientConfig(
        auth=CUSTOMER.static("public-demo-token"),
        key_provider=signing_key,
    )
    with Client(
        base_url="https://api.store.example",
        handler=HttpxHandler(raw_client, owns_client=True),
        config=config,
    ) as client:
        result = PaymentsApi(client).create(
            body=CreatePayment(
                order_id="order-42",
                card=Card(number="4111111111111111", cvv="123"),
                amount=12_900,
            )
        )

    print(result.id, result.status, result.receipt)


if __name__ == "__main__":
    main()
