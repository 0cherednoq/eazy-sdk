from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from zapros.websocket import (
    BinaryMessage,
    CloseMessage,
    PingMessage,
    PongMessage,
    TextMessage,
)

from eazy_sdk.core.kernel import Malformed, NoMatch, ParsedValue
from eazy_sdk.websocket import (
    ChannelKey,
    CloseDisposition,
    ConnectionGeneration,
    ControlEvent,
    ControlKind,
    CorrelationKey,
    EncodedFrame,
    FrameKind,
    FrameLimits,
    FrameTooLargeError,
    FrozenArray,
    FrozenObject,
    InboundMessageKind,
    JsonEventProtocol,
    JsonTextCodec,
    ProtocolConfigurationError,
    ProtocolMessage,
    frame_from_zapros,
    frame_to_zapros,
    freeze_value,
    thaw_value,
)
from tests.websocket._support import FakeWebSocket

ROOT = Path(__file__).parents[2]


def test_frozen_value_preserves_empty_array_and_object_identity() -> None:
    frozen_array = freeze_value([])
    frozen_object = freeze_value({})

    assert isinstance(frozen_array, FrozenArray)
    assert isinstance(frozen_object, FrozenObject)
    assert thaw_value(frozen_array) == []
    assert thaw_value(frozen_object) == {}


def test_json_text_codec_is_deterministic_and_round_trips() -> None:
    codec = JsonTextCodec()
    value = freeze_value({"z": [2, 1], "a": {"unicode": "Крым"}})

    encoded = codec.encode(value)
    decoded = codec.decode(frame_from_zapros(frame_to_zapros(encoded)))

    assert encoded == EncodedFrame(
        '{"a":{"unicode":"Крым"},"z":[2,1]}',
        FrameKind.TEXT,
    )
    assert decoded == ParsedValue(value)


def test_size_limit_runs_before_json_decode() -> None:
    codec = JsonTextCodec(max_inbound_bytes=4)
    result = codec.decode(frame_from_zapros(TextMessage("{broken")))

    assert isinstance(result, Malformed)
    assert isinstance(result.cause, FrameTooLargeError)
    with pytest.raises(FrameTooLargeError, match="text frame is 8 bytes"):
        frame_from_zapros(TextMessage("12345678"), limits=FrameLimits(max_text_bytes=4))


def test_zapros_frame_normalization_distinguishes_every_frame_kind() -> None:
    assert frame_from_zapros(TextMessage("text")).kind is FrameKind.TEXT
    assert frame_from_zapros(BinaryMessage(b"binary")).kind is FrameKind.BINARY
    assert frame_from_zapros(PingMessage(b"ping")).kind is FrameKind.PING
    assert frame_from_zapros(PongMessage(b"pong")).kind is FrameKind.PONG
    close = frame_from_zapros(CloseMessage(1001, "away"))
    assert close.kind is FrameKind.CLOSE
    assert close.close_code == 1001
    assert close.close_reason == "away"


def _protocol() -> JsonEventProtocol:
    return JsonEventProtocol(
        event_field="type",
        payload_field="data",
        correlation_field="id",
        channel_field="topic",
        controls=(
            ControlEvent("ready", ControlKind.READY),
            ControlEvent("complete", ControlKind.COMPLETE),
        ),
        recovery_event="recover",
        recovery_token_field="offset",
        fatal_close_codes=frozenset({4401}),
        reconnect_close_codes=frozenset({1012}),
    )


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        ({"type": "ready", "data": {}}, InboundMessageKind.CONTROL),
        ({"type": "result", "id": "call-1", "data": {"ok": True}}, InboundMessageKind.REPLY),
        ({"type": "tick", "topic": "prices", "data": 42}, InboundMessageKind.EVENT),
        ({"type": "notice", "data": "hello"}, InboundMessageKind.MESSAGE),
    ],
)
def test_json_event_protocol_classifies_application_messages(
    payload: dict[str, object], kind: InboundMessageKind
) -> None:
    protocol = _protocol()
    frame = frame_from_zapros(frame_to_zapros(protocol.codec.encode(freeze_value(payload))))
    inspected = protocol.inspect(frame)

    assert isinstance(inspected, ParsedValue)
    assert inspected.value.kind is kind


def test_protocol_distinguishes_binary_malformed_unknown_and_control_frames() -> None:
    protocol = _protocol()

    binary = protocol.inspect(frame_from_zapros(BinaryMessage(b"{}")))
    malformed = protocol.inspect(frame_from_zapros(TextMessage('{"type":"tick"}')))
    unknown = protocol.inspect(frame_from_zapros(TextMessage('{"data":{}}')))
    ping = protocol.inspect(frame_from_zapros(PingMessage(b"heartbeat")))

    assert isinstance(binary, NoMatch)
    assert isinstance(malformed, Malformed)
    assert isinstance(unknown, NoMatch)
    assert ping == ParsedValue(
        ProtocolMessage(
            InboundMessageKind.CONTROL,
            None,
            "heartbeat",
            control=ControlKind.PING,
        )
    )


def test_outbound_recovery_and_close_policy_are_explicit() -> None:
    protocol = _protocol()
    outbound = protocol.build_outbound(
        "subscribe",
        freeze_value({"symbol": "BTCUSDT"}),
        correlation=CorrelationKey("sub-1"),
        channel=ChannelKey("prices"),
    )
    recovery = protocol.build_recovery(ChannelKey("prices"), "offset-9")

    assert thaw_value(outbound) == {
        "type": "subscribe",
        "data": {"symbol": "BTCUSDT"},
        "id": "sub-1",
        "topic": "prices",
    }
    assert thaw_value(recovery) == {
        "type": "recover",
        "data": {"offset": "offset-9"},
        "topic": "prices",
    }
    assert protocol.classify_close(1000) is CloseDisposition.NORMAL
    assert protocol.classify_close(1012) is CloseDisposition.RECONNECT
    assert protocol.classify_close(4401) is CloseDisposition.FATAL
    assert ConnectionGeneration(0).value == 0


def test_json_event_protocol_rejects_implicit_or_colliding_configuration() -> None:
    parameters = inspect.signature(JsonEventProtocol).parameters
    assert parameters["event_field"].default is inspect.Parameter.empty
    assert parameters["payload_field"].default is inspect.Parameter.empty

    with pytest.raises(ProtocolConfigurationError, match="distinct"):
        JsonEventProtocol(event_field="type", payload_field="type")
    with pytest.raises(ProtocolConfigurationError, match="configured together"):
        JsonEventProtocol(
            event_field="type",
            payload_field="data",
            recovery_event="recover",
        )


async def test_encoded_frames_use_fake_zapros_connection_without_schema_knowledge() -> None:
    websocket = FakeWebSocket()
    frame = JsonTextCodec().encode(freeze_value({"type": "ping"}))

    await websocket.send(frame_to_zapros(frame))

    assert websocket.sent == [TextMessage('{"type":"ping"}')]


def test_protocol_and_codec_layers_do_not_own_io_or_runtime_registries() -> None:
    websocket_root = ROOT / "eazy_sdk" / "websocket"
    protocol_source = (websocket_root / "protocols.py").read_text(encoding="utf-8")
    codec_source = (websocket_root / "codecs.py").read_text(encoding="utf-8")
    protocol_tree = ast.parse(protocol_source)

    called_attributes = {
        node.func.attr
        for node in ast.walk(protocol_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"send", "recv"}.isdisjoint(called_attributes)
    assert "pending" not in codec_source.casefold()
    assert "reconnect" not in codec_source.casefold()
    fake_imports = (ROOT / "tests" / "websocket" / "_support" / "fake_websocket.py").read_text(
        encoding="utf-8"
    )
    assert "eazy_sdk.websocket" not in fake_imports
