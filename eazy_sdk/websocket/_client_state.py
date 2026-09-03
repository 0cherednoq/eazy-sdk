"""WebSocket client state records, options and pure helpers shared by the runtime mixins."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Self

from zapros import AsyncClient as ZaprosAsyncClient
from zapros.websocket import (
    AsyncBaseWebSocket,
    Message,
)

from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoDirection,
    CryptoInputScope,
    CryptoRegistry,
    CryptoStage,
    PayloadCrypto,
    WebSocketCryptoContext,
    WebSocketEncrypted,
)
from eazy_sdk.crypto._runtime import CompiledPayloadCrypto
from eazy_sdk.dependencies import DependencyRegistry
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters

from ._artifacts import (
    ChannelKey,
    ConnectionGeneration,
    CorrelationKey,
    FrozenValue,
    MessageReservedOutput,
    PreparedMessage,
)
from .auth import DynamicPerMessageAuth, ProtocolAuth
from .frames import DEFAULT_FRAME_LIMITS, FrameLimits
from .middleware import (
    ConnectionMiddlewareApplication,
    MessageMiddlewareApplication,
    SubscriptionMiddlewareApplication,
    WsDirection,
    WsMiddlewareContext,
    WsOutput,
    apply_message_patch,
)
from .policies import (
    OverflowPolicy,
    ReconnectPolicy,
    ResubscribePolicy,
    WsReplayPolicy,
)
from .protection import (
    ExactFrameTransform,
    InboundMessageTransform,
    OutboundMessageTransform,
    ProtectionSnapshot,
    compile_message_transforms,
)
from .protocols import (
    ProtocolMessage,
    WsProtocol,
)
from .schemas import (
    Messages,
    OutboundPayload,
    Replies,
)
from .subscriptions import Subscription


class WsSessionState(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    READY = "ready"
    RECONNECTING = "reconnecting"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WsClientConfig:
    writer_queue_capacity: int = 64
    call_timeout: float | None = 30.0
    frame_limits: FrameLimits = DEFAULT_FRAME_LIMITS
    reconnect: ReconnectPolicy = field(default_factory=ReconnectPolicy)
    heartbeat_interval: float | None = None
    heartbeat_timeout: float = 10.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters, repr=False)
    outbound_message_transforms: tuple[OutboundMessageTransform, ...] = ()
    inbound_message_transforms: tuple[InboundMessageTransform, ...] = ()
    outbound_frame_transforms: tuple[ExactFrameTransform, ...] = ()
    inbound_frame_transforms: tuple[ExactFrameTransform, ...] = ()
    protocol_auth: ProtocolAuth | None = None
    dynamic_per_message_auth: tuple[DynamicPerMessageAuth, ...] = ()
    connection_middleware: tuple[ConnectionMiddlewareApplication, ...] = ()
    message_middleware: tuple[MessageMiddlewareApplication, ...] = ()
    subscription_middleware: tuple[SubscriptionMiddlewareApplication, ...] = ()
    crypto: CryptoRegistry | PayloadCrypto | None = None
    crypto_wire: WebSocketEncrypted | None = None
    dependencies: DependencyRegistry = field(default_factory=DependencyRegistry, repr=False)
    message_reserved_outputs: tuple[MessageReservedOutput, ...] = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.writer_queue_capacity <= 0:
            raise ValueError("writer_queue_capacity must be positive")
        if self.call_timeout is not None and self.call_timeout <= 0:
            raise ValueError("call_timeout must be positive or None")
        if self.heartbeat_interval is not None and self.heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive or None")
        if self.heartbeat_timeout <= 0:
            raise ValueError("heartbeat_timeout must be positive")
        reserved = list(compile_message_transforms(self.outbound_message_transforms))
        paths = {item.path for item in reserved}
        for application in self.dynamic_per_message_auth:
            if application.output_path in paths:
                raise ValueError(
                    "duplicate message authentication/protection output path: "
                    f"{'.'.join(application.output_path)}"
                )
            paths.add(application.output_path)
            reserved.append(MessageReservedOutput(application, application.output_path))
        object.__setattr__(self, "message_reserved_outputs", tuple(reserved))


@dataclass(frozen=True, slots=True)
class WsCallOptions:
    timeout: float | None = None
    replay: WsReplayPolicy | None = None

    def __post_init__(self) -> None:
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive or None")


class WsConnector(Protocol):
    def __call__(
        self,
        url: object,
        *,
        client: ZaprosAsyncClient | None = None,
        subprotocols: list[str] | None = None,
        permessage_deflate: object = False,
    ) -> AbstractAsyncContextManager[AsyncBaseWebSocket]: ...


@dataclass(frozen=True, slots=True)
class _PendingKey:
    generation: ConnectionGeneration
    protocol_namespace: int
    correlation: CorrelationKey


@dataclass(slots=True)
class _PendingExchange:
    key: _PendingKey
    reply: asyncio.Future[ProtocolMessage]
    send_started: bool = False
    replies: Replies | None = None
    crypto: CompiledPayloadCrypto | None = None
    crypto_wire: WebSocketEncrypted | None = None


@dataclass(slots=True)
class _WriteItem:
    frame: Message
    completion: asyncio.Future[None]
    pending: _PendingExchange | None = None
    started: bool = False


@dataclass(slots=True)
class _SubscriptionRecord:
    subscription: Subscription[object]
    discriminator: str
    payload: FrozenValue
    correlation: CorrelationKey | None
    channel: ChannelKey | None
    outbound: OutboundPayload
    messages: Messages | None
    crypto: CompiledPayloadCrypto | None = None
    crypto_wire: WebSocketEncrypted | None = None


class _AttemptDeliveryFailure(Exception):
    def __init__(self, cause: BaseException, *, may_have_been_sent: bool) -> None:
        self.cause = cause
        self.may_have_been_sent = may_have_been_sent
        super().__init__(str(cause))


type MessageHandler = Callable[[ProtocolMessage], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class _ResolvedMessageAuth:
    output_path: tuple[str, ...]
    value: object = field(repr=False)
    name: str = "dynamic-per-message-auth"

    def protect(self, message: PreparedMessage) -> PreparedMessage:
        return apply_message_patch(message, (WsOutput(self.output_path, self.value),))


def _reply_models(replies: Replies | None) -> tuple[object, ...]:
    if replies is None:
        return ()
    return tuple(case.model for case in replies.cases)


def _message_models(messages: Messages | None) -> tuple[object, ...]:
    if messages is None:
        return ()
    return tuple(case.model for case in messages.cases)


def _outbound_crypto_stages(
    selected: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None,
) -> tuple[str, ...]:
    if selected is None or selected[0].profile.outbound is None:
        return ()
    outbound = selected[0].profile.outbound
    stages: list[str] = []
    if outbound.fields:
        stages.append("document")
    if outbound.encoded is not None:
        stages.append("encoded")
    return tuple(stages)


def _validate_websocket_crypto(
    compiled: CompiledPayloadCrypto,
    wire: WebSocketEncrypted,
    reserved: tuple[MessageReservedOutput, ...],
) -> None:
    profile = compiled.profile
    encoded = (profile.outbound is not None and profile.outbound.encoded is not None) or (
        profile.inbound is not None and profile.inbound.encoded is not None
    )
    if encoded and any(item.scope is CryptoInputScope.OPERATION for item in profile.inputs):
        raise CryptoConfigurationError(
            "WebSocket whole-frame crypto accepts only connection-scoped inputs; "
            "operation routing is unavailable before inbound decryption"
        )
    if (
        profile.outbound is not None
        and profile.outbound.encoded is not None
        and profile.outbound.encoded.outputs
    ):
        raise CryptoConfigurationError(
            "WebSocket whole-frame metadata must be carried inside the algorithm byte envelope"
        )
    declared: list[object] = []
    if profile.outbound is not None:
        for item in profile.outbound.fields:
            declared.extend(item.outputs)
    bound = {id(item.output) for item in wire.metadata}
    inbound_metadata: list[object] = []
    if profile.inbound is not None:
        for inbound_field in profile.inbound.fields:
            inbound_metadata.extend(inbound_field.metadata)
        if profile.inbound.encoded is not None and profile.inbound.encoded.metadata:
            raise CryptoConfigurationError(
                "WebSocket whole-frame decrypt cannot read detached protocol metadata"
            )
    required_outputs = {id(item) for item in (*declared, *inbound_metadata)}
    if required_outputs != bound:
        raise CryptoConfigurationError(
            "WebSocket crypto metadata bindings must exactly match document-stage writes and reads"
        )
    for binding in wire.metadata:
        for owner in reserved:
            common = min(len(binding.path), len(owner.path))
            if binding.path[:common] == owner.path[:common]:
                raise CryptoConfigurationError(
                    "WebSocket crypto metadata conflicts with another reserved writer: "
                    + ".".join(binding.path)
                )


class _WsClientBase:
    """Attributes shared by the runtime mixins; ``AsyncWsClient.__init__`` assigns them."""

    endpoint: str
    protocol: WsProtocol
    subprotocols: tuple[str, ...]
    permessage_deflate: object
    config: WsClientConfig
    _zapros_client: ZaprosAsyncClient
    _owns_zapros_client: bool
    _connector: WsConnector
    _on_message: MessageHandler | None
    _state: WsSessionState
    _generation: ConnectionGeneration
    _protocol_namespace: int
    _next_correlation: int
    _connect_lock: asyncio.Lock
    _failure_lock: asyncio.Lock
    _connection_context: AbstractAsyncContextManager[AsyncBaseWebSocket] | None
    _websocket: AsyncBaseWebSocket | None
    _writer_queue: asyncio.Queue[_WriteItem] | None
    _reader_task: asyncio.Task[None] | None
    _writer_task: asyncio.Task[None] | None
    _heartbeat_task: asyncio.Task[None] | None
    _reconnect_task: asyncio.Task[None] | None
    _heartbeat_ack: asyncio.Event
    _protocol_ready: asyncio.Event
    _active_write: _WriteItem | None
    _pending: dict[_PendingKey, _PendingExchange]
    _subscriptions_by_channel: dict[tuple[int, ChannelKey], _SubscriptionRecord]
    _subscriptions_by_correlation: dict[tuple[int, CorrelationKey], _SubscriptionRecord]
    _handler_tasks: set[asyncio.Task[None]]
    _last_protection_snapshot: ProtectionSnapshot | None
    _inbound_crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None
    _crypto_connection_values: dict[int, object]

    # --- cross-mixin contract (implemented by the runtime mixins) ---
    async def __aenter__(self) -> Self:
        raise NotImplementedError

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        raise NotImplementedError

    async def connect(self) -> None:
        raise NotImplementedError

    async def _run_connection_middleware(
        self,
        generation: ConnectionGeneration,
    ) -> None:
        raise NotImplementedError

    def _middleware_context(
        self,
        *,
        operation: str | None,
        channel: str | None,
        event: str | None,
        direction: WsDirection | None,
        generation: ConnectionGeneration | None = None,
    ) -> WsMiddlewareContext:
        raise NotImplementedError

    async def _writer_loop(
        self,
        websocket: AsyncBaseWebSocket,
        generation: ConnectionGeneration,
    ) -> None:
        raise NotImplementedError

    async def _heartbeat_loop(self, generation: ConnectionGeneration) -> None:
        raise NotImplementedError

    async def _reader_loop(
        self,
        websocket: AsyncBaseWebSocket,
        generation: ConnectionGeneration,
    ) -> None:
        raise NotImplementedError

    async def _route_message(
        self,
        message: ProtocolMessage,
        generation: ConnectionGeneration,
    ) -> None:
        raise NotImplementedError

    async def _publish_subscription(
        self,
        record: _SubscriptionRecord,
        message: ProtocolMessage,
        generation: ConnectionGeneration,
    ) -> None:
        raise NotImplementedError

    def _enqueue_control(self, message: Message) -> None:
        raise NotImplementedError

    async def _dispatch_protocol_error(self, cause: Exception) -> None:
        raise NotImplementedError

    def _dispatch_user_message(self, message: ProtocolMessage) -> None:
        raise NotImplementedError

    async def _teardown_connection(self) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError

    async def send(
        self,
        discriminator: str,
        payload: object,
        *,
        replay: WsReplayPolicy | None = None,
        payload_schema: OutboundPayload | None = None,
        crypto: PayloadCrypto | None = None,
        crypto_wire: WebSocketEncrypted | None = None,
        crypto_inherit: bool = True,
        operation_id: str | None = None,
    ) -> None:
        raise NotImplementedError

    async def call(
        self,
        discriminator: str,
        payload: object,
        *,
        replay: WsReplayPolicy | None = None,
        timeout: float | None = None,
        payload_schema: OutboundPayload | None = None,
        replies: Replies | None = None,
        crypto: PayloadCrypto | None = None,
        crypto_wire: WebSocketEncrypted | None = None,
        crypto_inherit: bool = True,
        operation_id: str | None = None,
    ) -> object:
        raise NotImplementedError

    async def subscribe(
        self,
        discriminator: str,
        payload: object,
        *,
        channel: str | None = None,
        resubscribe: ResubscribePolicy | None = None,
        queue_capacity: int = 64,
        overflow: OverflowPolicy = OverflowPolicy.FAIL,
        payload_schema: OutboundPayload | None = None,
        messages: Messages | None = None,
        crypto: PayloadCrypto | None = None,
        crypto_wire: WebSocketEncrypted | None = None,
        crypto_inherit: bool = True,
        operation_id: str | None = None,
    ) -> Subscription[object]:
        raise NotImplementedError

    async def _execute_operation(
        self,
        declaration: object,
        values: dict[str, object],
        *,
        options: WsCallOptions | None,
    ) -> object:
        raise NotImplementedError

    async def _send_once(
        self,
        discriminator: str,
        payload: object,
        *,
        payload_schema: OutboundPayload | None,
        crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None,
        operation_id: str,
    ) -> None:
        raise NotImplementedError

    async def _prepare_outbound(
        self,
        discriminator: str,
        payload: object,
        *,
        correlation: CorrelationKey | None,
        channel: ChannelKey | None,
        payload_schema: OutboundPayload | None,
        crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None = None,
        operation_id: str | None = None,
    ) -> Message:
        raise NotImplementedError

    async def _prepare_envelope(
        self,
        envelope: FrozenValue,
        semantic_payload: FrozenValue,
        *,
        correlation: CorrelationKey | None,
        channel: ChannelKey | None,
        operation: str,
        crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None = None,
        operation_id: str | None = None,
    ) -> Message:
        raise NotImplementedError

    async def _resolved_transforms(self) -> tuple[OutboundMessageTransform, ...]:
        raise NotImplementedError

    async def _apply_outbound_transforms(
        self,
        prepared: PreparedMessage,
        *,
        operation: str,
    ) -> tuple[PreparedMessage, tuple[OutboundMessageTransform, ...]]:
        raise NotImplementedError

    async def _cancel_subscription(self, record: _SubscriptionRecord) -> None:
        raise NotImplementedError

    async def _send_subscription(
        self,
        record: _SubscriptionRecord,
        *,
        recovery: bool,
    ) -> None:
        raise NotImplementedError

    async def _call_once(
        self,
        discriminator: str,
        payload: object,
        *,
        timeout: float | None,
        payload_schema: OutboundPayload | None,
        replies: Replies | None,
        crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None,
        operation_id: str,
    ) -> object:
        raise NotImplementedError

    def _enqueue(self, item: _WriteItem, *, allow_handshaking: bool = False) -> None:
        raise NotImplementedError

    def _subscription_records(self) -> tuple[_SubscriptionRecord, ...]:
        raise NotImplementedError

    def _remove_subscription(self, record: _SubscriptionRecord) -> None:
        raise NotImplementedError

    def _allocate_correlation(self) -> CorrelationKey:
        raise NotImplementedError

    @staticmethod
    def _raise_delivery(discriminator: str, failure: _AttemptDeliveryFailure) -> None:
        raise NotImplementedError

    @property
    def last_protection_snapshot(self) -> ProtectionSnapshot | None:
        raise NotImplementedError

    def _compile_operation_crypto(
        self,
        profile: PayloadCrypto | None,
        wire: WebSocketEncrypted | None,
        *,
        inherit: bool,
        operation_id: str,
        channel: str | None,
        event: str | None,
        outbound: OutboundPayload,
        inbound_models: tuple[object, ...] = (),
    ) -> tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None:
        raise NotImplementedError

    def _crypto_reserved_outputs(
        self, wire: WebSocketEncrypted
    ) -> tuple[MessageReservedOutput, ...]:
        raise NotImplementedError

    def _activate_inbound_crypto(
        self,
        compiled: CompiledPayloadCrypto,
        wire: WebSocketEncrypted,
    ) -> None:
        raise NotImplementedError

    async def _crypto_context(
        self,
        compiled: CompiledPayloadCrypto,
        operation_id: str,
        event: str | None,
        channel: ChannelKey | None,
        direction: CryptoDirection,
        stage: CryptoStage,
        *,
        generation: ConnectionGeneration | None = None,
        frame_kind: str | None = None,
    ) -> WebSocketCryptoContext:
        raise NotImplementedError

    async def _send_protocol_auth(self, application: ProtocolAuth) -> None:
        raise NotImplementedError

    async def _send_protocol_envelope(
        self,
        envelope: FrozenValue,
        payload: FrozenValue,
        *,
        correlation: CorrelationKey | None,
        channel: ChannelKey | None,
        operation: str,
        allow_handshaking: bool = False,
    ) -> None:
        raise NotImplementedError

    async def _restore_subscriptions(self, generation: ConnectionGeneration) -> None:
        raise NotImplementedError

    async def _fail_connection(
        self,
        cause: BaseException,
        generation: ConnectionGeneration,
        *,
        fatal: bool = False,
        reconnect: bool = True,
    ) -> None:
        raise NotImplementedError

    def _disconnect_subscriptions(
        self,
        cause: BaseException,
        *,
        fatal: bool,
        reconnect: bool,
    ) -> bool:
        raise NotImplementedError

    def _schedule_reconnect(self) -> None:
        raise NotImplementedError

    async def _reconnect_loop(self) -> None:
        raise NotImplementedError

    async def _prepare_replay(self) -> None:
        raise NotImplementedError

    def _fail_outstanding(self, cause: BaseException) -> None:
        raise NotImplementedError
