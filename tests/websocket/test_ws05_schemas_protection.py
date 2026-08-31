from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass, replace

import pytest
from zapros.websocket import TextMessage

from eazy_sdk._internal.kernel import Malformed, MalformedCase, ParsedValue
from eazy_sdk.websocket import (
    AsyncWsApi,
    AsyncWsClient,
    ConnectionGeneration,
    CustomMessageSignature,
    EncodedFrame,
    ErrorReply,
    Event,
    ExactFrameTransform,
    FrameKind,
    HmacSha256MessageSignature,
    InboundFrame,
    InboundMessageKind,
    JsonEventProtocol,
    JsonPayload,
    MalformedMessageError,
    Message,
    Messages,
    Nonce,
    PerMessageAuth,
    PreparedMessage,
    ProtocolMessage,
    RemoteMessageError,
    ReplayWithDeduplication,
    Replies,
    SecretBytes,
    SecretText,
    SuccessReply,
    Timestamp,
    WsClientConfig,
    freeze_value,
    thaw_value,
    ws,
)
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks


@dataclass(frozen=True, slots=True)
class CreateOrder:
    symbol: str
    quantity: int


@dataclass(frozen=True, slots=True)
class CreatedOrder:
    order_id: str
    accepted: bool


@dataclass(frozen=True, slots=True)
class OrderProblem:
    code: str


def _protocol() -> JsonEventProtocol:
    return JsonEventProtocol(
        event_field="type",
        payload_field="data",
        correlation_field="id",
        channel_field="topic",
    )


async def _wait_for_sends(connection: LiveFakeWebSocket, count: int) -> None:
    for _ in range(200):
        if len(connection.sent) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"expected {count} sends, got {len(connection.sent)}")


async def test_json_payload_and_reply_cases_use_common_model_adapters() -> None:
    connection = LiveFakeWebSocket()
    replies = Replies(
        success=(SuccessReply("created", CreatedOrder),),
        errors=(ErrorReply("problem", OrderProblem),),
    )

    class Orders(AsyncWsApi):
        @ws.call("create", payload=JsonPayload(CreateOrder), replies=replies)
        async def create(self, symbol: str, quantity: int) -> CreatedOrder:
            raise NotImplementedError

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        call = asyncio.create_task(Orders(client).create("BTCUSDT", 2))
        await _wait_for_sends(connection, 1)
        assert connection.sent == [
            TextMessage('{"data":{"quantity":2,"symbol":"BTCUSDT"},"id":"1","type":"create"}')
        ]
        connection.feed(
            TextMessage('{"type":"created","id":"1","data":{"order_id":"o-1","accepted":true}}')
        )
        assert await call == CreatedOrder("o-1", True)


def test_recognized_invalid_reply_is_malformed_not_no_match() -> None:
    replies = Replies(success=(SuccessReply("created", CreatedOrder),))
    message = ProtocolMessage(
        kind=InboundMessageKind.REPLY,
        discriminator="created",
        payload=freeze_value({"order_id": "o-1"}),
    )

    inspected = replies.inspect(message)

    assert isinstance(inspected, MalformedCase)
    assert isinstance(inspected.malformed, Malformed)


async def test_runtime_materializes_malformed_reply_and_typed_subscription_message() -> None:
    connection = LiveFakeWebSocket()
    replies = Replies(success=(SuccessReply("created", CreatedOrder),))
    messages = Messages((Message("tick", CreatedOrder),))

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        call = asyncio.create_task(client.call("create", {}, replies=replies))
        await _wait_for_sends(connection, 1)
        connection.feed(TextMessage('{"type":"created","id":"1","data":{"order_id":"x"}}'))
        with pytest.raises(MalformedMessageError):
            await call

        subscription = await client.subscribe(
            "subscribe",
            {},
            channel="orders",
            messages=messages,
        )
        connection.feed(
            TextMessage(
                '{"type":"tick","topic":"orders","data":{"order_id":"o-2","accepted":true}}'
            )
        )
        assert await anext(subscription) == Event(
            CreatedOrder("o-2", True),
            client.generation,
            recovered=False,
        )


async def test_documented_error_reply_keeps_typed_payload() -> None:
    connection = LiveFakeWebSocket()
    replies = Replies(
        success=(SuccessReply("created", CreatedOrder),),
        errors=(ErrorReply("problem", OrderProblem),),
    )
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        call = asyncio.create_task(client.call("create", {}, replies=replies))
        await _wait_for_sends(connection, 1)
        connection.feed(TextMessage('{"type":"problem","id":"1","data":{"code":"LIMIT"}}'))
        with pytest.raises(RemoteMessageError) as captured:
            await call
        assert captured.value.error == OrderProblem("LIMIT")


class _PrefixTransform:
    def __init__(self, prefix: str) -> None:
        self.name = f"prefix-{prefix}"
        self.prefix = prefix

    def protect(self, frame: EncodedFrame) -> EncodedFrame:
        assert isinstance(frame.data, str)
        return EncodedFrame(self.prefix + frame.data, FrameKind.TEXT)

    def unprotect(self, frame: InboundFrame) -> InboundFrame:
        assert isinstance(frame.data, str)
        if not frame.data.startswith(self.prefix):
            raise ValueError("missing protected prefix")
        return replace(frame, data=frame.data.removeprefix(self.prefix))


async def test_semantic_hmac_and_exact_frame_protection_are_distinct_golden_stages() -> None:
    connection = LiveFakeWebSocket()
    key = SecretBytes(b"signing-key")
    config = WsClientConfig(
        outbound_message_transforms=(
            Timestamp(("timestamp",), clock=lambda: 1700000000),
            Nonce(("nonce",), factory=lambda: "n-1"),
            PerMessageAuth(("auth",), SecretText("token-value")),
            HmacSha256MessageSignature(key=key, output_path=("signature",)),
        ),
        outbound_frame_transforms=(_PrefixTransform("A:"), _PrefixTransform("B:")),
        inbound_frame_transforms=(_PrefixTransform("B:"), _PrefixTransform("A:")),
    )

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
            config=config,
        ) as client,
    ):
        call = asyncio.create_task(client.call("lookup", {"value": 1}))
        await _wait_for_sends(connection, 1)
        sent = connection.sent[0]
        assert isinstance(sent, TextMessage)
        assert sent.data.startswith("B:A:")
        semantic = sent.data.removeprefix("B:A:")
        unsigned = (
            '{"auth":"token-value","data":{"value":1},"id":"1","nonce":"n-1",'
            '"timestamp":1700000000,"type":"lookup"}'
        )
        expected_signature = hmac.new(b"signing-key", unsigned.encode(), hashlib.sha256).hexdigest()
        expected = json.loads(unsigned)
        expected["signature"] = expected_signature
        assert semantic == json.dumps(
            expected,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        protected_reply = 'B:A:{"type":"result","id":"1","data":"ok"}'
        connection.feed(TextMessage(protected_reply))
        assert await call == "ok"

        snapshot = client.last_protection_snapshot
        assert snapshot is not None
        assert snapshot.semantic_transforms == (
            "timestamp",
            "nonce",
            "per-message-auth",
            "hmac-sha256-message",
        )
        assert "token-value" not in repr(snapshot)
        assert expected_signature not in repr(snapshot)

    assert "signing-key" not in repr(key)
    assert "token-value" not in repr(config)


class _CustomSigner:
    def sign(self, message: PreparedMessage) -> object:
        assert thaw_value(message.payload) == {"value": 1}
        return "custom-signature"


def test_custom_signer_only_returns_declared_output_and_artifact_repr_is_redacted() -> None:
    prepared = PreparedMessage(
        envelope=freeze_value({"type": "lookup", "data": {"secret": "do-not-print"}}),
        payload=freeze_value({"value": 1}),
        correlation=None,
        channel=None,
        generation=ConnectionGeneration(1),
    )
    protected = CustomMessageSignature(
        signer=_CustomSigner(),
        output_path=("signature",),
    ).protect(prepared)

    protected_envelope = thaw_value(protected.envelope)
    assert isinstance(protected_envelope, dict)
    assert protected_envelope["signature"] == "custom-signature"
    assert "do-not-print" not in repr(prepared)
    assert "custom-signature" not in repr(protected)


class _PayloadUnwrapper:
    name = "payload-unwrapper"

    def unprotect(self, message: ProtocolMessage) -> ParsedValue[ProtocolMessage] | Malformed:
        payload = thaw_value(message.payload)
        if not isinstance(payload, dict) or "wrapped" not in payload:
            return Malformed(ValueError("missing wrapped payload"))
        return ParsedValue(replace(message, payload=freeze_value(payload["wrapped"])))


async def test_semantic_unprotection_runs_before_reply_model_validation() -> None:
    connection = LiveFakeWebSocket()
    replies = Replies(success=(SuccessReply("created", CreatedOrder),))
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
            config=WsClientConfig(inbound_message_transforms=(_PayloadUnwrapper(),)),
        ) as client,
    ):
        call = asyncio.create_task(client.call("create", {}, replies=replies))
        await _wait_for_sends(connection, 1)
        connection.feed(
            TextMessage(
                '{"type":"created","id":"1","data":{"wrapped":{"order_id":"o-3","accepted":true}}}'
            )
        )
        assert await call == CreatedOrder("o-3", True)


async def test_permitted_replay_rebuilds_nonce_and_signature() -> None:
    first = LiveFakeWebSocket(send_failures=[RuntimeError("reset")])
    second = LiveFakeWebSocket()
    nonces = iter(("n-1", "n-2"))
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([first, second]),
        config=WsClientConfig(
            outbound_message_transforms=(
                Nonce(("nonce",), factory=lambda: next(nonces)),
                HmacSha256MessageSignature(
                    key=SecretBytes(b"key"),
                    output_path=("signature",),
                ),
            )
        ),
    )
    async with assert_no_task_leaks():
        call = asyncio.create_task(
            client.call(
                "create",
                {"deduplication_key": "one"},
                replay=ReplayWithDeduplication("one"),
            )
        )
        await _wait_for_sends(second, 1)
        second.feed(TextMessage('{"type":"created","id":"2","data":"ok"}'))
        assert await call == "ok"
        await client.aclose()

    assert isinstance(first.sent[0], TextMessage)
    assert isinstance(second.sent[0], TextMessage)
    assert '"nonce":"n-1"' in first.sent[0].data
    assert '"nonce":"n-2"' in second.sent[0].data
    assert first.sent[0].data != second.sent[0].data


def test_signature_seals_semantic_message_against_later_mutation() -> None:
    prepared = PreparedMessage(
        freeze_value({"type": "lookup", "data": {}}),
        freeze_value({}),
        None,
        None,
        ConnectionGeneration(1),
    )
    from eazy_sdk.websocket.protection import apply_outbound_message_transforms

    with pytest.raises(ValueError, match="after signature"):
        apply_outbound_message_transforms(
            prepared,
            (
                HmacSha256MessageSignature(key=SecretBytes(b"key")),
                Timestamp(("late",), clock=lambda: 1),
            ),
        )

    with pytest.raises(ValueError, match="duplicate message protection output path"):
        WsClientConfig(
            outbound_message_transforms=(
                Timestamp(("same",), clock=lambda: 1),
                Nonce(("same",), factory=lambda: "n"),
            )
        )


def test_core_exports_exact_transform_protocol_but_no_builtin_encryption_algorithm() -> None:
    import eazy_sdk.websocket as websocket

    assert ExactFrameTransform is not None
    assert not any("encrypt" in name.casefold() for name in websocket.__all__)
