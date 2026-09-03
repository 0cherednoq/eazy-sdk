from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import cast

import pytest
from pydantic import BaseModel
from zapros.websocket import BinaryMessage, TextMessage

from eazy_sdk.crypto import (
    CryptoContext,
    CryptoDirection,
    CryptoStage,
    EncryptedFrameKindMismatchError,
    FrozenValue,
    WebSocketCryptoContext,
    decrypt_encoded,
    decrypt_field,
    decrypt_inbound,
    encrypt_encoded,
    encrypt_field,
    encrypt_outbound,
    payload_crypto,
    websocket_encrypted,
)
from eazy_sdk.crypto._runtime import compile_payload_crypto
from eazy_sdk.models import default_model_adapters
from eazy_sdk.websocket import (
    AsyncWsApi,
    AsyncWsClient,
    EncodedFrame,
    Event,
    FrameKind,
    InboundFrame,
    JsonEventProtocol,
    JsonPayload,
    Message,
    Messages,
    ReplayWithDeduplication,
    Replies,
    SuccessReply,
    WsClientConfig,
    ws,
)
from eazy_sdk.websocket._crypto import unprotect_ws_frame
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks


class CreatePayment(BaseModel):
    number: str
    amount: int


class PaymentResult(BaseModel):
    payment_id: str
    receipt: str


@dataclass(frozen=True)
class FieldCipher:
    name: str = "ws-field-test-only"

    def encrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert isinstance(value, str)
        return "field:" + value

    def decrypt(self, value: FrozenValue, *, context: CryptoContext) -> FrozenValue:
        assert isinstance(value, str)
        return value.removeprefix("field:")


@dataclass(frozen=True)
class FrameCipher:
    name: str = "ws-frame-test-only"

    def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        return b"frame:" + base64.b64encode(value)

    def decrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
        if not value.startswith(b"frame:"):
            raise ValueError("not an encrypted test frame")
        return base64.b64decode(value.removeprefix(b"frame:"))


FIELD = FieldCipher()
FRAME = FrameCipher()
PROFILE = payload_crypto(
    "ws-payments-v1",
    outbound=encrypt_outbound(
        encrypt_field(CreatePayment, lambda body: body.number, using=FIELD),
        encoded=encrypt_encoded(using=FRAME),
    ),
    inbound=decrypt_inbound(
        decrypt_field(PaymentResult, lambda body: body.receipt, using=FIELD),
        encoded=decrypt_encoded(using=FRAME),
    ),
)
WIRE = websocket_encrypted(frame_kind="binary")
TEXT_WIRE = websocket_encrypted(frame_kind="text", text_safe=True)
REPLIES = Replies((SuccessReply("created", PaymentResult),))


class PaymentsSocket(AsyncWsApi):
    @ws.call(
        "create",
        payload=JsonPayload(CreatePayment),
        replies=REPLIES,
        crypto=PROFILE,
        crypto_wire=WIRE,
    )
    async def create(self, number: str, amount: int) -> PaymentResult:
        raise AssertionError("declaration body must not execute")


class TextPaymentsSocket(AsyncWsApi):
    @ws.call(
        "create",
        payload=JsonPayload(CreatePayment),
        replies=REPLIES,
        crypto=PROFILE,
        crypto_wire=TEXT_WIRE,
    )
    async def create(self, number: str, amount: int) -> PaymentResult:
        raise AssertionError("declaration body must not execute")


def _protocol() -> JsonEventProtocol:
    return JsonEventProtocol(
        event_field="type",
        payload_field="data",
        correlation_field="id",
    )


async def _wait_for_send(connection: LiveFakeWebSocket) -> BinaryMessage:
    for _ in range(100):
        if connection.sent:
            return cast(BinaryMessage, connection.sent[0])
        await asyncio.sleep(0)
    raise AssertionError("expected one encrypted WebSocket send")


def _decrypt_frame(data: bytes) -> dict[str, object]:
    clear = base64.b64decode(data.removeprefix(b"frame:"))
    return cast(dict[str, object], json.loads(clear))


def _encrypt_frame(value: object) -> bytes:
    clear = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return b"frame:" + base64.b64encode(clear)


async def test_one_profile_encrypts_http_shaped_models_and_websocket_frames() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        task = asyncio.create_task(PaymentsSocket(client).create("4111", 500))
        sent = await _wait_for_send(connection)
        assert isinstance(sent, BinaryMessage)
        envelope = _decrypt_frame(sent.data)
        assert envelope["type"] == "create"
        assert envelope["data"] == {"number": "field:4111", "amount": 500}
        correlation = envelope["id"]

        connection.feed(
            BinaryMessage(
                _encrypt_frame(
                    {
                        "type": "created",
                        "id": correlation,
                        "data": {
                            "payment_id": "pay-1",
                            "receipt": "field:receipt-1",
                        },
                    }
                )
            )
        )
        result = await task

    assert result == PaymentResult(payment_id="pay-1", receipt="receipt-1")


async def test_text_safe_wire_policy_captures_text_ciphertext_frame() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        task = asyncio.create_task(TextPaymentsSocket(client).create("4111", 500))
        for _ in range(100):
            if connection.sent:
                break
            await asyncio.sleep(0)
        sent = cast(TextMessage, connection.sent[0])
        assert isinstance(sent, TextMessage)
        envelope = _decrypt_frame(sent.data.encode())
        correlation = envelope["id"]
        connection.feed(
            TextMessage(
                _encrypt_frame(
                    {
                        "type": "created",
                        "id": correlation,
                        "data": {
                            "payment_id": "pay-text",
                            "receipt": "field:receipt-text",
                        },
                    }
                ).decode()
            )
        )
        result = await task

    assert result == PaymentResult(payment_id="pay-text", receipt="receipt-text")


async def test_field_only_websocket_crypto_preserves_json_text_frame() -> None:
    field_profile = payload_crypto(
        "ws-fields-only-v1",
        outbound=encrypt_outbound(
            encrypt_field(CreatePayment, lambda body: body.number, using=FIELD)
        ),
    )
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        await client.send(
            "notify",
            {"number": "4111", "amount": 500},
            crypto=field_profile,
        )

    assert isinstance(connection.sent[0], TextMessage)
    assert json.loads(connection.sent[0].data)["data"]["number"] == "field:4111"


async def test_reconnect_reencrypts_replayed_exchange_with_new_generation_context() -> None:
    @dataclass
    class GenerationCipher:
        name: str = "ws-generation-test-only"
        generations: list[int] = field(default_factory=list)

        def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
            self.generations.append(context.attempt)
            return f"enc:{len(self.generations)}:".encode() + base64.b64encode(value)

        def decrypt(self, value: bytes, *, context: CryptoContext) -> bytes:
            return base64.b64decode(value.split(b":", 2)[2])

    cipher = GenerationCipher()
    replay_profile = payload_crypto(
        "ws-replay-v1",
        outbound=encrypt_outbound(encoded=encrypt_encoded(using=cipher)),
        inbound=decrypt_inbound(encoded=decrypt_encoded(using=cipher)),
    )
    first = LiveFakeWebSocket(send_failures=[RuntimeError("reset")])
    second = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([first, second]),
        ) as client,
    ):
        task = asyncio.create_task(
            client.call(
                "create",
                {"deduplication_key": "one"},
                crypto=replay_profile,
                crypto_wire=WIRE,
                replay=ReplayWithDeduplication("one", max_replays=1),
            )
        )
        for _ in range(100):
            if second.sent:
                break
            await asyncio.sleep(0)
        assert isinstance(first.sent[0], BinaryMessage)
        assert isinstance(second.sent[0], BinaryMessage)
        assert first.sent[0].data != second.sent[0].data
        envelope = json.loads(base64.b64decode(second.sent[0].data.split(b":", 2)[2]))
        reply = json.dumps(
            {"type": "created", "id": envelope["id"], "data": {"ok": True}},
            separators=(",", ":"),
        ).encode()
        second.feed(BinaryMessage(b"enc:server:" + base64.b64encode(reply)))
        assert await task == {"ok": True}

    assert cipher.generations == [1, 2]


async def test_subscription_decrypts_frame_and_field_before_message_model_validation() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=JsonEventProtocol(
                event_field="type",
                payload_field="data",
                correlation_field="id",
                channel_field="topic",
            ),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        subscription = await client.subscribe(
            "subscribe-payments",
            {"number": "4111", "amount": 500},
            channel="payments",
            messages=Messages((Message("created", PaymentResult),)),
            crypto=PROFILE,
            crypto_wire=WIRE,
        )
        sent = await _wait_for_send(connection)
        assert _decrypt_frame(sent.data)["data"] == {
            "number": "field:4111",
            "amount": 500,
        }
        connection.feed(
            BinaryMessage(
                _encrypt_frame(
                    {
                        "type": "created",
                        "topic": "payments",
                        "data": {
                            "payment_id": "pay-sub",
                            "receipt": "field:receipt-sub",
                        },
                    }
                )
            )
        )
        assert await anext(subscription) == Event(
            PaymentResult(payment_id="pay-sub", receipt="receipt-sub"),
            client.generation,
            recovered=False,
        )
        await subscription.aclose()


async def test_exact_frame_transform_observes_ciphertext_that_is_sent() -> None:
    @dataclass
    class CaptureExactFrame:
        name: str = "capture-exact-test-only"
        outbound: list[bytes] = field(default_factory=list)

        def protect(self, frame: EncodedFrame) -> EncodedFrame:
            assert isinstance(frame.data, bytes)
            self.outbound.append(frame.data)
            return frame

        def unprotect(self, frame: InboundFrame) -> InboundFrame:
            return frame

    capture = CaptureExactFrame()
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
            config=WsClientConfig(outbound_frame_transforms=(capture,)),
        ) as client,
    ):
        await client.send(
            "notify",
            {"number": "4111", "amount": 500},
            crypto=PROFILE,
            crypto_wire=WIRE,
        )

    assert isinstance(connection.sent[0], BinaryMessage)
    assert capture.outbound == [connection.sent[0].data]


async def test_inbound_encrypted_frame_kind_mismatch_fails_closed() -> None:
    compiled = compile_payload_crypto(
        PROFILE,
        default_model_adapters(),
        inbound_models=(PaymentResult,),
    )
    context = WebSocketCryptoContext(
        "create",
        PROFILE.name,
        "pending",
        CryptoDirection.INBOUND,
        CryptoStage.ENCODED,
        1,
    )

    with pytest.raises(EncryptedFrameKindMismatchError, match="expected encrypted binary"):
        await unprotect_ws_frame(
            InboundFrame(FrameKind.TEXT, "encrypted"),
            compiled,
            WIRE,
            context=context,
        )
