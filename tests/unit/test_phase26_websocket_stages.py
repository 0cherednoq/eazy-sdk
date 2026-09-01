from __future__ import annotations

from zapros.websocket import TextMessage

from eazy_sdk.websocket._artifacts import (
    ChannelKey,
    ConnectionGeneration,
    CorrelationKey,
    PreparedMessage,
    freeze_value,
)
from eazy_sdk.websocket._runtime_stages import (
    ConnectAction,
    DisconnectAction,
    FailureState,
    ReaderRoute,
    ReconnectAction,
    RecoveryMode,
    WritePreparation,
    connect_action,
    disconnect_action,
    failure_state,
    prepare_write,
    reconnect_action,
    recovery_decision,
    route_reader_message,
    write_admitted,
)
from eazy_sdk.websocket.codecs import JsonTextCodec
from eazy_sdk.websocket.policies import (
    NeverResubscribe,
    RecoverBySequence,
    ResubscribeFromStart,
)
from eazy_sdk.websocket.protocols import (
    ControlKind,
    InboundMessageKind,
    ProtocolMessage,
)


def _message(
    kind: InboundMessageKind,
    *,
    correlation: CorrelationKey | None = None,
    channel: ChannelKey | None = None,
    control: ControlKind | None = None,
    terminal_error: Exception | None = None,
) -> ProtocolMessage:
    return ProtocolMessage(
        kind,
        "event",
        freeze_value({"value": 1}),
        correlation=correlation,
        channel=channel,
        control=control,
        terminal_error=terminal_error,
    )


def test_failure_state_is_an_effect_free_lifecycle_decision() -> None:
    assert failure_state(fatal=True, reconnect=True) is FailureState.FAILED
    assert failure_state(fatal=False, reconnect=True) is FailureState.RECONNECTING
    assert failure_state(fatal=False, reconnect=False) is FailureState.IDLE
    assert connect_action("ready") is ConnectAction.RETURN_READY
    assert connect_action("closed") is ConnectAction.REJECT_CLOSED
    assert connect_action("idle") is ConnectAction.START
    assert reconnect_action("ready") is ReconnectAction.STOP
    assert reconnect_action("reconnecting") is ReconnectAction.ATTEMPT
    assert write_admitted("ready", allow_handshaking=False, queue_present=True)
    assert write_admitted("handshaking", allow_handshaking=True, queue_present=True)
    assert not write_admitted("handshaking", allow_handshaking=False, queue_present=True)


def test_reader_routing_preserves_pending_subscription_and_control_priority() -> None:
    correlation = CorrelationKey("one")
    channel = ChannelKey("orders")
    error = RuntimeError("remote")

    terminal = route_reader_message(
        _message(
            InboundMessageKind.REPLY,
            correlation=correlation,
            terminal_error=error,
        ),
        pending_present=True,
        pending_open=True,
        correlation_subscription=True,
        channel_subscription=False,
        complete_subscription=False,
    )
    assert terminal.route is ReaderRoute.PENDING_ERROR
    assert terminal.discard_pending

    reply = route_reader_message(
        _message(InboundMessageKind.REPLY, correlation=correlation),
        pending_present=False,
        pending_open=False,
        correlation_subscription=True,
        channel_subscription=False,
        complete_subscription=False,
    )
    assert reply.route is ReaderRoute.SUBSCRIPTION_MESSAGE

    event = route_reader_message(
        _message(InboundMessageKind.EVENT, channel=channel),
        pending_present=False,
        pending_open=False,
        correlation_subscription=False,
        channel_subscription=True,
        complete_subscription=False,
    )
    assert event.route is ReaderRoute.SUBSCRIPTION_MESSAGE

    complete = route_reader_message(
        _message(InboundMessageKind.CONTROL, channel=channel, control=ControlKind.COMPLETE),
        pending_present=False,
        pending_open=False,
        correlation_subscription=False,
        channel_subscription=True,
        complete_subscription=True,
    )
    assert complete.route is ReaderRoute.SUBSCRIPTION_COMPLETE


def test_disconnect_and_recovery_plans_do_not_own_socket_io() -> None:
    assert (
        disconnect_action(ResubscribeFromStart(), fatal=True, reconnect=True)
        is DisconnectAction.FAIL_FATAL
    )
    assert (
        disconnect_action(ResubscribeFromStart(), fatal=False, reconnect=False)
        is DisconnectAction.FAIL_ENDED
    )
    assert (
        disconnect_action(NeverResubscribe(), fatal=False, reconnect=True)
        is DisconnectAction.FAIL_DISABLED
    )
    assert (
        disconnect_action(RecoverBySequence("sequence"), fatal=False, reconnect=True)
        is DisconnectAction.RETAIN
    )

    missing_channel = recovery_decision(
        requested=True,
        channel=None,
        token=freeze_value("42"),
    )
    assert missing_channel.mode is RecoveryMode.RECOVER
    assert missing_channel.error == "protocol recovery requires a channel-routed subscription"
    assert recovery_decision(
        requested=True,
        channel=ChannelKey("orders"),
        token=None,
    ).mode is RecoveryMode.RESUBSCRIBE
    recovered = recovery_decision(
        requested=True,
        channel=ChannelKey("orders"),
        token=freeze_value("42"),
    )
    assert recovered.mode is RecoveryMode.RECOVER
    assert recovered.token == "42"


async def test_write_stage_returns_frozen_frame_and_diagnostics() -> None:
    envelope = freeze_value({"type": "create", "payload": {"id": 7}})
    payload = freeze_value({"id": 7})
    seen: list[PreparedMessage] = []

    async def prepare_semantic(
        prepared: PreparedMessage,
    ) -> tuple[PreparedMessage, tuple[()]]:
        seen.append(prepared)
        return prepared, ()

    result = await prepare_write(
        WritePreparation(
            envelope=envelope,
            payload=payload,
            correlation=CorrelationKey("one"),
            channel=None,
            generation=ConnectionGeneration(3),
            codec=JsonTextCodec(),
            exact_transforms=(),
        ),
        prepare_semantic=prepare_semantic,
    )

    assert seen[0].generation == ConnectionGeneration(3)
    assert isinstance(result.frame, TextMessage)
    assert result.frame.data == '{"payload":{"id":7},"type":"create"}'
    assert result.snapshot.generation == 3
    assert result.snapshot.semantic_transforms == ()
