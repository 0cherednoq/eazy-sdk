"""Typed, protocol-neutral decisions for the WebSocket runtime coordinator."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from zapros.websocket import Message

from eazy_sdk.crypto import WebSocketCryptoContext, WebSocketEncrypted
from eazy_sdk.crypto._runtime import CompiledPayloadCrypto

from ._artifacts import (
    ChannelKey,
    ConnectionGeneration,
    CorrelationKey,
    FrozenValue,
    PreparedMessage,
)
from ._crypto import protect_ws_frame
from .codecs import WsCodec
from .frames import frame_to_zapros
from .policies import NeverResubscribe, ResubscribePolicy
from .protection import (
    ExactFrameTransform,
    OutboundMessageTransform,
    ProtectionSnapshot,
    apply_outbound_frame_transforms,
    protection_snapshot,
)
from .protocols import ControlKind, InboundMessageKind, ProtocolMessage


class FailureState(Enum):
    FAILED = "failed"
    RECONNECTING = "reconnecting"
    IDLE = "idle"


class ConnectAction(Enum):
    RETURN_READY = "return-ready"
    REJECT_CLOSED = "reject-closed"
    START = "start"


class ReconnectAction(Enum):
    STOP = "stop"
    ATTEMPT = "attempt"


class ReaderRoute(Enum):
    PENDING_ERROR = "pending-error"
    SUBSCRIPTION_ERROR = "subscription-error"
    PENDING_REPLY = "pending-reply"
    SUBSCRIPTION_MESSAGE = "subscription-message"
    READY = "ready"
    PONG = "pong"
    PING = "ping"
    SUBSCRIPTION_COMPLETE = "subscription-complete"
    USER_MESSAGE = "user-message"


class DisconnectAction(Enum):
    FAIL_FATAL = "fail-fatal"
    FAIL_ENDED = "fail-ended"
    FAIL_DISABLED = "fail-disabled"
    RETAIN = "retain"


class RecoveryMode(Enum):
    RESUBSCRIBE = "resubscribe"
    RECOVER = "recover"


@dataclass(frozen=True, slots=True)
class ReaderRouteDecision:
    route: ReaderRoute
    discard_pending: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    mode: RecoveryMode
    token: FrozenValue | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WritePreparation:
    envelope: FrozenValue
    payload: FrozenValue
    correlation: CorrelationKey | None
    channel: ChannelKey | None
    generation: ConnectionGeneration
    codec: WsCodec
    exact_transforms: tuple[ExactFrameTransform, ...]
    crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None = None
    crypto_context: CryptoContextPreparation | None = None
    crypto_stages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedWrite:
    frame: Message
    snapshot: ProtectionSnapshot


type SemanticWritePreparation = Callable[
    [PreparedMessage],
    Awaitable[tuple[PreparedMessage, tuple[OutboundMessageTransform, ...]]],
]
type CryptoContextPreparation = Callable[[str], Awaitable[WebSocketCryptoContext]]


def failure_state(*, fatal: bool, reconnect: bool) -> FailureState:
    if fatal:
        return FailureState.FAILED
    if reconnect:
        return FailureState.RECONNECTING
    return FailureState.IDLE


def connect_action(state: str) -> ConnectAction:
    if state == "ready":
        return ConnectAction.RETURN_READY
    if state in {"closing", "closed"}:
        return ConnectAction.REJECT_CLOSED
    return ConnectAction.START


def reconnect_action(state: str) -> ReconnectAction:
    if state in {"closing", "closed", "ready"}:
        return ReconnectAction.STOP
    return ReconnectAction.ATTEMPT


def write_admitted(
    state: str,
    *,
    allow_handshaking: bool,
    queue_present: bool,
) -> bool:
    permitted = state == "ready" or (allow_handshaking and state == "handshaking")
    return permitted and queue_present


def route_reader_message(
    message: ProtocolMessage,
    *,
    pending_present: bool,
    pending_open: bool,
    correlation_subscription: bool,
    channel_subscription: bool,
    complete_subscription: bool,
) -> ReaderRouteDecision:
    correlated_terminal = message.terminal_error is not None and message.correlation is not None
    correlated_reply = (
        message.kind is InboundMessageKind.REPLY and message.correlation is not None
    )
    discard_pending = pending_present and (correlated_terminal or correlated_reply)
    if correlated_terminal:
        if pending_open:
            return ReaderRouteDecision(ReaderRoute.PENDING_ERROR, discard_pending)
        if correlation_subscription:
            return ReaderRouteDecision(ReaderRoute.SUBSCRIPTION_ERROR, discard_pending)
    if correlated_reply:
        if pending_open:
            return ReaderRouteDecision(ReaderRoute.PENDING_REPLY, discard_pending)
        if correlation_subscription:
            return ReaderRouteDecision(ReaderRoute.SUBSCRIPTION_MESSAGE, discard_pending)
    if (
        message.kind is InboundMessageKind.EVENT
        and message.channel is not None
        and channel_subscription
    ):
        return ReaderRouteDecision(ReaderRoute.SUBSCRIPTION_MESSAGE, discard_pending)
    if message.kind is InboundMessageKind.CONTROL:
        if message.control is ControlKind.READY:
            return ReaderRouteDecision(ReaderRoute.READY, discard_pending)
        if message.control is ControlKind.PONG:
            return ReaderRouteDecision(ReaderRoute.PONG, discard_pending)
        if message.control is ControlKind.PING:
            return ReaderRouteDecision(ReaderRoute.PING, discard_pending)
        if message.control is ControlKind.COMPLETE and complete_subscription:
            return ReaderRouteDecision(ReaderRoute.SUBSCRIPTION_COMPLETE, discard_pending)
    return ReaderRouteDecision(ReaderRoute.USER_MESSAGE, discard_pending)


def disconnect_action(
    policy: ResubscribePolicy,
    *,
    fatal: bool,
    reconnect: bool,
) -> DisconnectAction:
    if fatal:
        return DisconnectAction.FAIL_FATAL
    if not reconnect:
        return DisconnectAction.FAIL_ENDED
    if isinstance(policy, NeverResubscribe):
        return DisconnectAction.FAIL_DISABLED
    return DisconnectAction.RETAIN


def recovery_decision(
    *,
    requested: bool,
    channel: ChannelKey | None,
    token: FrozenValue | None,
) -> RecoveryDecision:
    if not requested:
        return RecoveryDecision(RecoveryMode.RESUBSCRIBE)
    if channel is None:
        return RecoveryDecision(
            RecoveryMode.RECOVER,
            error="protocol recovery requires a channel-routed subscription",
        )
    if token is None:
        return RecoveryDecision(RecoveryMode.RESUBSCRIBE)
    return RecoveryDecision(RecoveryMode.RECOVER, token=token)


async def prepare_write(
    stage: WritePreparation,
    *,
    prepare_semantic: SemanticWritePreparation,
) -> PreparedWrite:
    prepared = PreparedMessage(
        stage.envelope,
        stage.payload,
        stage.correlation,
        stage.channel,
        stage.generation,
    )
    protected, semantic_transforms = await prepare_semantic(prepared)
    encoded = stage.codec.encode(protected.envelope)
    if stage.crypto is not None:
        if stage.crypto_context is None:
            raise RuntimeError("WebSocket crypto preparation requires a crypto context")
        encoded = await protect_ws_frame(
            encoded,
            stage.crypto[0],
            stage.crypto[1],
            context=await stage.crypto_context(encoded.kind.value),
        )
    protected_frame = apply_outbound_frame_transforms(encoded, stage.exact_transforms)
    snapshot = protection_snapshot(
        protected,
        protected_frame,
        semantic=semantic_transforms,
        exact=stage.exact_transforms,
        crypto_profile=stage.crypto[0].profile.name if stage.crypto is not None else None,
        crypto_stages=stage.crypto_stages,
    )
    return PreparedWrite(frame_to_zapros(protected_frame), snapshot)


__all__: list[str] = []
