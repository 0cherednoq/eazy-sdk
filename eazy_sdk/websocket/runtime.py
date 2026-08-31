"""Async WebSocket session runtime over the Zapros connection boundary."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, cast

from zapros import AsyncBaseHandler
from zapros import AsyncClient as ZaprosAsyncClient
from zapros.websocket import (
    AsyncBaseWebSocket,
    CloseCode,
    Message,
    PingMessage,
    PongMessage,
    aconnect_ws,
)

from eazy_sdk._internal.kernel import Malformed, ParsedValue
from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoDirection,
    CryptoInputScope,
    CryptoOutputValue,
    CryptoRegistry,
    CryptoStage,
    CryptoValues,
    PayloadCrypto,
    WebSocketCryptoContext,
    WebSocketEncrypted,
)
from eazy_sdk.crypto._inputs import resolve_crypto_inputs
from eazy_sdk.crypto._runtime import CompiledPayloadCrypto, compile_payload_crypto
from eazy_sdk.dependencies import DependencyRegistry
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters

from ._artifacts import (
    ChannelKey,
    ConnectionGeneration,
    CorrelationKey,
    FrameKind,
    FrozenValue,
    MessageReservedOutput,
    PreparedMessage,
    WsOperationKind,
    freeze_value,
    thaw_value,
)
from ._crypto import (
    apply_ws_crypto_metadata,
    protect_ws_document,
    protect_ws_frame,
    unprotect_ws_frame,
    unprotect_ws_message,
)
from .auth import DynamicPerMessageAuth, ProtocolAuth, StaticUpgradeAuth
from .errors import (
    DeliveryNotSentError,
    DeliveryUnknownError,
    SubscriptionDisconnectedError,
    WsCallTimeoutError,
    WsClientClosedError,
    WsConnectError,
    WsQueueOverflowError,
)
from .frames import DEFAULT_FRAME_LIMITS, FrameLimits, frame_from_zapros, frame_to_zapros
from .middleware import (
    ConnectionMiddlewareApplication,
    MessageMiddlewareApplication,
    SubscriptionMiddlewareApplication,
    WsDirection,
    WsMiddlewareContext,
    WsOutput,
    apply_message_patch,
    evaluate_middleware,
)
from .policies import (
    NeverReplay,
    NeverResubscribe,
    OverflowPolicy,
    ReconnectPolicy,
    RecoverBySequence,
    RecoverByToken,
    ResubscribePolicy,
    WsReplayPolicy,
    replay_allowed,
)
from .protection import (
    ExactFrameTransform,
    InboundMessageTransform,
    OutboundMessageTransform,
    ProtectionSnapshot,
    apply_inbound_frame_transforms,
    apply_inbound_message_transforms,
    apply_outbound_frame_transforms,
    apply_outbound_message_transforms,
    compile_message_transforms,
    protection_snapshot,
)
from .protocols import (
    CloseDisposition,
    ControlKind,
    InboundMessageKind,
    ProtocolMessage,
    WsProtocol,
)
from .schemas import (
    JsonPayload,
    Messages,
    OutboundPayload,
    Replies,
    selected_value,
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


class AsyncWsClient:
    """One-connection async runtime with generation-safe one-shot exchanges."""

    def __init__(
        self,
        *,
        endpoint: str,
        protocol: WsProtocol,
        zapros_client: ZaprosAsyncClient | None = None,
        zapros_handler: AsyncBaseHandler | None = None,
        upgrade_auth: StaticUpgradeAuth | None = None,
        subprotocols: tuple[str, ...] = (),
        permessage_deflate: object = False,
        config: WsClientConfig | None = None,
        connector: WsConnector | None = None,
        on_message: MessageHandler | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("WebSocket endpoint cannot be empty")
        self.endpoint = endpoint
        self.protocol = protocol
        self.subprotocols = subprotocols
        self.permessage_deflate = permessage_deflate
        self.config = config or WsClientConfig()
        if zapros_client is not None and (zapros_handler is not None or upgrade_auth is not None):
            raise ValueError("zapros_client cannot be combined with zapros_handler or upgrade_auth")
        self._zapros_client = zapros_client or ZaprosAsyncClient(
            handler=zapros_handler,
            default_headers=upgrade_auth.headers() if upgrade_auth is not None else None,
        )
        self._owns_zapros_client = zapros_client is None
        self._connector = connector or cast(WsConnector, aconnect_ws)
        self._on_message = on_message

        self._state = WsSessionState.IDLE
        self._generation = ConnectionGeneration(0)
        self._protocol_namespace = id(protocol)
        self._next_correlation = 0
        self._connect_lock = asyncio.Lock()
        self._failure_lock = asyncio.Lock()
        self._connection_context: AbstractAsyncContextManager[AsyncBaseWebSocket] | None = None
        self._websocket: AsyncBaseWebSocket | None = None
        self._writer_queue: asyncio.Queue[_WriteItem] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._heartbeat_ack = asyncio.Event()
        self._protocol_ready = asyncio.Event()
        self._active_write: _WriteItem | None = None
        self._pending: dict[_PendingKey, _PendingExchange] = {}
        self._subscriptions_by_channel: dict[tuple[int, ChannelKey], _SubscriptionRecord] = {}
        self._subscriptions_by_correlation: dict[
            tuple[int, CorrelationKey], _SubscriptionRecord
        ] = {}
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._last_protection_snapshot: ProtectionSnapshot | None = None
        self._inbound_crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None = None
        self._crypto_connection_values: dict[int, object] = {}
        if isinstance(self.config.crypto, PayloadCrypto):
            default_wire = self.config.crypto_wire or WebSocketEncrypted()
            default_compiled = compile_payload_crypto(self.config.crypto, self.config.models)
            _validate_websocket_crypto(
                default_compiled,
                default_wire,
                self._crypto_reserved_outputs(default_wire),
            )
            self._activate_inbound_crypto(default_compiled, default_wire)

    @property
    def state(self) -> WsSessionState:
        return self._state

    @property
    def generation(self) -> ConnectionGeneration:
        return self._generation

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def last_protection_snapshot(self) -> ProtectionSnapshot | None:
        return self._last_protection_snapshot

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
        selected_profile = profile
        selected_wire = wire
        if selected_profile is None and inherit:
            configured = self.config.crypto
            if isinstance(configured, PayloadCrypto):
                selected_profile = configured
                selected_wire = selected_wire or self.config.crypto_wire
            elif isinstance(configured, CryptoRegistry):
                resolved = configured.resolve_websocket(
                    endpoint=self.endpoint,
                    operation_id=operation_id,
                    channel=channel,
                    event=event,
                    direction=CryptoDirection.OUTBOUND,
                )
                if resolved is not None:
                    selected_profile = resolved.profile
                    if resolved.wire is not None:
                        if not isinstance(resolved.wire, WebSocketEncrypted):
                            raise CryptoConfigurationError(
                                "WebSocket crypto rule requires a WebSocketEncrypted binding"
                            )
                        selected_wire = resolved.wire
        if selected_profile is None:
            return None
        actual_wire = selected_wire or self.config.crypto_wire or WebSocketEncrypted()
        outbound_model: object | None = None
        if isinstance(outbound, JsonPayload) and outbound.model is not object:
            outbound_model = outbound.model
        elif selected_profile.outbound is not None and selected_profile.outbound.fields:
            outbound_model = object
        compiled = compile_payload_crypto(
            selected_profile,
            self.config.models,
            outbound_model=outbound_model,
            inbound_models=inbound_models,
        )
        _validate_websocket_crypto(
            compiled,
            actual_wire,
            self._crypto_reserved_outputs(actual_wire),
        )
        self._activate_inbound_crypto(compiled, actual_wire)
        return compiled, actual_wire

    def _crypto_reserved_outputs(
        self, wire: WebSocketEncrypted
    ) -> tuple[MessageReservedOutput, ...]:
        protocol_paths = getattr(self.protocol, "crypto_reserved_paths", None)
        if wire.metadata and protocol_paths is None:
            raise CryptoConfigurationError(
                "WebSocket protocol must declare crypto_reserved_paths before metadata binding"
            )
        protocol_reserved = tuple(
            MessageReservedOutput(self.protocol, path) for path in protocol_paths or ()
        )
        return (*self.config.message_reserved_outputs, *protocol_reserved)

    def _activate_inbound_crypto(
        self,
        compiled: CompiledPayloadCrypto,
        wire: WebSocketEncrypted,
    ) -> None:
        inbound = compiled.profile.inbound
        if inbound is None or inbound.encoded is None:
            return
        existing = self._inbound_crypto
        if existing is not None and (
            existing[0].profile != compiled.profile or existing[1] != wire
        ):
            raise CryptoConfigurationError(
                "one WebSocket connection cannot select multiple inbound encoded crypto profiles"
            )
        self._inbound_crypto = (compiled, wire)

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
        connection_inputs = tuple(
            item for item in compiled.profile.inputs if item.scope is CryptoInputScope.CONNECTION
        )
        operation_inputs = tuple(
            item
            for item in compiled.profile.inputs
            if item.scope is CryptoInputScope.OPERATION and stage is CryptoStage.DOCUMENT
        )
        connection_values, connection_aad = await resolve_crypto_inputs(
            connection_inputs,
            self.config.dependencies,
            operation_id="connection",
            attempt=(generation or self._generation).value,
            cache=self._crypto_connection_values,
        )
        operation_values, operation_aad = await resolve_crypto_inputs(
            operation_inputs,
            self.config.dependencies,
            operation_id=operation_id,
            attempt=(generation or self._generation).value,
        )
        values = CryptoValues((*connection_values.items, *operation_values.items))
        return WebSocketCryptoContext(
            operation_id,
            compiled.profile.name,
            "pending",
            direction,
            stage,
            (generation or self._generation).value,
            aad=(*connection_aad, *operation_aad),
            values=values,
            endpoint=self.endpoint,
            protocol=type(self.protocol).__name__,
            channel=channel.value if channel is not None else None,
            event=event,
            generation=(generation or self._generation).value,
            frame_kind=frame_kind,
        )

    async def __aenter__(self) -> AsyncWsClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def connect(self) -> None:
        restore = False
        async with self._connect_lock:
            if self._state is WsSessionState.READY:
                return
            if self._state in {WsSessionState.CLOSING, WsSessionState.CLOSED}:
                raise WsClientClosedError("WebSocket client is closed")
            await self._teardown_connection()
            self._state = WsSessionState.CONNECTING
            context = self._connector(
                self.endpoint,
                client=self._zapros_client,
                subprotocols=list(self.subprotocols) or None,
                permessage_deflate=self.permessage_deflate,
            )
            try:
                websocket = await context.__aenter__()
            except Exception as exc:
                self._state = WsSessionState.FAILED
                raise WsConnectError(
                    f"failed to connect WebSocket endpoint {self.endpoint!r}"
                ) from exc
            self._connection_context = context
            self._websocket = websocket
            self._generation = ConnectionGeneration(self._generation.value + 1)
            self._crypto_connection_values.clear()
            generation = self._generation
            self._writer_queue = asyncio.Queue(maxsize=self.config.writer_queue_capacity)
            self._state = WsSessionState.HANDSHAKING
            self._protocol_ready.clear()
            self._writer_task = asyncio.create_task(
                self._writer_loop(websocket, generation),
                name=f"eazy_sdk-ws-writer-{generation.value}",
            )
            self._reader_task = asyncio.create_task(
                self._reader_loop(websocket, generation),
                name=f"eazy_sdk-ws-reader-{generation.value}",
            )
            if self.config.heartbeat_interval is not None:
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(generation),
                    name=f"eazy_sdk-ws-heartbeat-{generation.value}",
                )
            try:
                await self._run_connection_middleware(generation)
                if self.config.protocol_auth is not None:
                    await self._send_protocol_auth(self.config.protocol_auth)
            except BaseException as exc:
                self._fail_outstanding(exc)
                await self._teardown_connection()
                self._state = WsSessionState.FAILED
                raise WsConnectError(
                    f"WebSocket handshake initialization failed for {self.endpoint!r}"
                ) from exc
            self._state = WsSessionState.READY
            restore = generation.value > 1 and bool(self._subscription_records())
        if restore:
            await self._restore_subscriptions(self._generation)

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
        policy = replay or NeverReplay()
        selected_crypto = self._compile_operation_crypto(
            crypto,
            crypto_wire,
            inherit=crypto_inherit,
            operation_id=operation_id or discriminator,
            channel=None,
            event=discriminator,
            outbound=payload_schema or JsonPayload(),
        )
        replays = 0
        while True:
            try:
                await self._send_once(
                    discriminator,
                    payload,
                    payload_schema=payload_schema,
                    crypto=selected_crypto,
                    operation_id=operation_id or discriminator,
                )
                return
            except _AttemptDeliveryFailure as failure:
                if replay_allowed(
                    policy,
                    may_have_been_sent=failure.may_have_been_sent,
                    replays=replays,
                ):
                    replays += 1
                    await self._prepare_replay()
                    continue
                self._raise_delivery(discriminator, failure)

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
        policy = replay or NeverReplay()
        effective_timeout = self.config.call_timeout if timeout is None else timeout
        selected_crypto = self._compile_operation_crypto(
            crypto,
            crypto_wire,
            inherit=crypto_inherit,
            operation_id=operation_id or discriminator,
            channel=None,
            event=discriminator,
            outbound=payload_schema or JsonPayload(),
            inbound_models=_reply_models(replies),
        )
        replays = 0
        while True:
            try:
                return await self._call_once(
                    discriminator,
                    payload,
                    timeout=effective_timeout,
                    payload_schema=payload_schema,
                    replies=replies,
                    crypto=selected_crypto,
                    operation_id=operation_id or discriminator,
                )
            except _AttemptDeliveryFailure as failure:
                if replay_allowed(
                    policy,
                    may_have_been_sent=failure.may_have_been_sent,
                    replays=replays,
                ):
                    replays += 1
                    await self._prepare_replay()
                    continue
                self._raise_delivery(discriminator, failure)

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
        selected_crypto = self._compile_operation_crypto(
            crypto,
            crypto_wire,
            inherit=crypto_inherit,
            operation_id=operation_id or discriminator,
            channel=channel,
            event=discriminator,
            outbound=payload_schema or JsonPayload(),
            inbound_models=_message_models(messages),
        )
        await self.connect()
        channel_key = ChannelKey(channel) if channel is not None else None
        await evaluate_middleware(
            self.config.subscription_middleware,
            self._middleware_context(
                operation=discriminator,
                channel=channel,
                event="subscribe",
                direction=WsDirection.OUTBOUND,
            ),
            allow_patch=False,
        )
        correlation = None if channel_key is not None else self._allocate_correlation()
        route: tuple[int, object]
        if channel_key is not None:
            route = (self._protocol_namespace, channel_key)
            registry: dict[tuple[int, object], _SubscriptionRecord] = cast(
                dict[tuple[int, object], _SubscriptionRecord],
                self._subscriptions_by_channel,
            )
        else:
            if correlation is None:
                raise AssertionError("correlation route was not allocated")
            route = (self._protocol_namespace, correlation)
            registry = cast(
                dict[tuple[int, object], _SubscriptionRecord],
                self._subscriptions_by_correlation,
            )
        if route in registry:
            raise ValueError(f"duplicate active subscription route: {route[1]!r}")

        record: _SubscriptionRecord

        async def close_subscription() -> None:
            await self._cancel_subscription(record)
            self._remove_subscription(record)

        subscription = Subscription[object](
            close=close_subscription,
            resubscribe=resubscribe or NeverResubscribe(),
            queue_capacity=queue_capacity,
            overflow=overflow,
            generation=self._generation,
        )
        outbound = payload_schema or JsonPayload()
        record = _SubscriptionRecord(
            subscription,
            discriminator,
            outbound.prepare(payload, self.config.models),
            correlation,
            channel_key,
            outbound,
            messages,
            selected_crypto[0] if selected_crypto is not None else None,
            selected_crypto[1] if selected_crypto is not None else None,
        )
        registry[route] = record
        try:
            await self._send_subscription(record, recovery=False)
        except BaseException:
            self._remove_subscription(record)
            raise
        return subscription

    async def _execute_operation(
        self,
        declaration: object,
        values: dict[str, object],
        *,
        options: WsCallOptions | None,
    ) -> object:
        from .api import _WsOperationDeclaration

        if not isinstance(declaration, _WsOperationDeclaration):
            raise TypeError("WebSocket client requires a WebSocket operation declaration")
        replay = options.replay if options and options.replay is not None else declaration.replay
        if declaration.kind is WsOperationKind.SEND:
            await self.send(
                declaration.discriminator,
                values,
                replay=replay,
                payload_schema=declaration.payload,
                crypto=declaration.crypto,
                crypto_wire=declaration.crypto_wire,
                crypto_inherit=declaration.crypto_inherit,
                operation_id=declaration.operation_id,
            )
            return None
        if declaration.kind is WsOperationKind.CALL:
            timeout = options.timeout if options is not None else None
            return await self.call(
                declaration.discriminator,
                values,
                replay=replay,
                timeout=timeout,
                payload_schema=declaration.payload,
                replies=declaration.replies,
                crypto=declaration.crypto,
                crypto_wire=declaration.crypto_wire,
                crypto_inherit=declaration.crypto_inherit,
                operation_id=declaration.operation_id,
            )
        if declaration.kind is WsOperationKind.SUBSCRIBE:
            return await self.subscribe(
                declaration.discriminator,
                values,
                resubscribe=declaration.resubscribe,
                payload_schema=declaration.payload,
                messages=declaration.messages,
                crypto=declaration.crypto,
                crypto_wire=declaration.crypto_wire,
                crypto_inherit=declaration.crypto_inherit,
                operation_id=declaration.operation_id,
            )
        raise TypeError(f"unsupported WebSocket operation kind: {declaration.kind.value}")

    async def _send_once(
        self,
        discriminator: str,
        payload: object,
        *,
        payload_schema: OutboundPayload | None,
        crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None,
        operation_id: str,
    ) -> None:
        await self.connect()
        frame = await self._prepare_outbound(
            discriminator,
            payload,
            correlation=None,
            channel=None,
            payload_schema=payload_schema,
            crypto=crypto,
            operation_id=operation_id,
        )
        completion = asyncio.get_running_loop().create_future()
        item = _WriteItem(frame, completion)
        self._enqueue(item)
        await completion

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
        semantic_payload = (payload_schema or JsonPayload()).prepare(payload, self.config.models)
        crypto_outputs: list[CryptoOutputValue[object]] = []
        if crypto is not None:
            semantic_payload = await protect_ws_document(
                semantic_payload,
                crypto[0],
                context=await self._crypto_context(
                    crypto[0],
                    operation_id or discriminator,
                    discriminator,
                    channel,
                    CryptoDirection.OUTBOUND,
                    CryptoStage.DOCUMENT,
                ),
                outputs=crypto_outputs,
            )
        envelope = self.protocol.build_outbound(
            discriminator,
            semantic_payload,
            correlation=correlation,
            channel=channel,
        )
        if crypto is not None:
            envelope = apply_ws_crypto_metadata(envelope, crypto[1], crypto_outputs)
        return await self._prepare_envelope(
            envelope,
            semantic_payload,
            correlation=correlation,
            channel=channel,
            operation=discriminator,
            crypto=crypto,
            operation_id=operation_id or discriminator,
        )

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
        prepared = PreparedMessage(
            envelope,
            semantic_payload,
            correlation,
            channel,
            self._generation,
        )
        protected, semantic_transforms = await self._apply_outbound_transforms(
            prepared,
            operation=operation,
        )
        encoded = self.protocol.codec.encode(protected.envelope)
        if crypto is not None:
            encoded = await protect_ws_frame(
                encoded,
                crypto[0],
                crypto[1],
                context=await self._crypto_context(
                    crypto[0],
                    operation_id or operation,
                    operation,
                    channel,
                    CryptoDirection.OUTBOUND,
                    CryptoStage.ENCODED,
                    frame_kind=encoded.kind.value,
                ),
            )
        protected_frame = apply_outbound_frame_transforms(
            encoded, self.config.outbound_frame_transforms
        )
        self._last_protection_snapshot = protection_snapshot(
            protected,
            protected_frame,
            semantic=semantic_transforms,
            exact=self.config.outbound_frame_transforms,
            crypto_profile=crypto[0].profile.name if crypto is not None else None,
            crypto_stages=_outbound_crypto_stages(crypto),
        )
        return frame_to_zapros(protected_frame)

    async def _resolved_transforms(self) -> tuple[OutboundMessageTransform, ...]:
        resolved: list[OutboundMessageTransform] = []
        for application in self.config.dynamic_per_message_auth:
            resolved.append(
                _ResolvedMessageAuth(
                    application.output_path,
                    await application.resolve(),
                    application.name,
                )
            )
        resolved.extend(self.config.outbound_message_transforms)
        return tuple(resolved)

    async def _apply_outbound_transforms(
        self,
        prepared: PreparedMessage,
        *,
        operation: str,
    ) -> tuple[PreparedMessage, tuple[OutboundMessageTransform, ...]]:
        context = self._middleware_context(
            operation=operation,
            channel=prepared.channel.value if prepared.channel is not None else None,
            event=operation,
            direction=WsDirection.OUTBOUND,
        )
        outputs = await evaluate_middleware(
            self.config.message_middleware,
            context,
            allow_patch=True,
        )
        reserved = {item.path for item in self.config.message_reserved_outputs}
        collision = next((output.path for output in outputs if output.path in reserved), None)
        if collision is not None:
            raise ValueError(
                f"middleware output collides with protected output: {'.'.join(collision)}"
            )
        patched = apply_message_patch(prepared, outputs)
        transforms = await self._resolved_transforms()
        return apply_outbound_message_transforms(patched, transforms), transforms

    async def _send_protocol_auth(self, application: ProtocolAuth) -> None:
        frame = await self._prepare_outbound(
            application.discriminator,
            await application.resolve(),
            correlation=None,
            channel=None,
            payload_schema=None,
        )
        completion = asyncio.get_running_loop().create_future()
        self._enqueue(_WriteItem(frame, completion), allow_handshaking=True)
        await completion
        if application.await_ready:
            await asyncio.wait_for(
                self._protocol_ready.wait(),
                timeout=application.ready_timeout,
            )

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
        frame = await self._prepare_envelope(
            envelope,
            payload,
            correlation=correlation,
            channel=channel,
            operation=operation,
        )
        completion = asyncio.get_running_loop().create_future()
        self._enqueue(_WriteItem(frame, completion), allow_handshaking=allow_handshaking)
        await completion

    async def _cancel_subscription(self, record: _SubscriptionRecord) -> None:
        if self._state is not WsSessionState.READY:
            return
        envelope = self.protocol.build_cancel(record.correlation, record.channel)
        if envelope is None:
            return
        await self._send_protocol_envelope(
            envelope,
            record.payload,
            correlation=record.correlation,
            channel=record.channel,
            operation="complete",
        )

    async def _run_connection_middleware(
        self,
        generation: ConnectionGeneration,
    ) -> None:
        await evaluate_middleware(
            self.config.connection_middleware,
            self._middleware_context(
                operation=None,
                channel=None,
                event="connect",
                direction=None,
                generation=generation,
            ),
            allow_patch=False,
        )

    def _middleware_context(
        self,
        *,
        operation: str | None,
        channel: str | None,
        event: str | None,
        direction: WsDirection | None,
        generation: ConnectionGeneration | None = None,
    ) -> WsMiddlewareContext:
        return WsMiddlewareContext(
            operation,
            self.endpoint,
            type(self.protocol).__name__,
            channel,
            event,
            direction,
            (generation or self._generation).value,
        )

    async def _send_subscription(
        self,
        record: _SubscriptionRecord,
        *,
        recovery: bool,
    ) -> None:
        semantic_payload = record.payload
        crypto_outputs: list[CryptoOutputValue[object]] = []
        if record.crypto is not None:
            semantic_payload = await protect_ws_document(
                semantic_payload,
                record.crypto,
                context=await self._crypto_context(
                    record.crypto,
                    record.discriminator,
                    record.discriminator,
                    record.channel,
                    CryptoDirection.OUTBOUND,
                    CryptoStage.DOCUMENT,
                ),
                outputs=crypto_outputs,
            )
        if recovery:
            if record.channel is None:
                raise SubscriptionDisconnectedError(
                    "protocol recovery requires a channel-routed subscription"
                )
            token = record.subscription._recovery_token()
            if token is None:
                recovery = False
                envelope = self.protocol.build_outbound(
                    record.discriminator,
                    semantic_payload,
                    correlation=record.correlation,
                    channel=record.channel,
                )
            else:
                built = self.protocol.build_recovery(record.channel, token)
                if built is None:
                    raise SubscriptionDisconnectedError(
                        "protocol does not support subscription recovery messages"
                    )
                envelope = built
        else:
            envelope = self.protocol.build_outbound(
                record.discriminator,
                semantic_payload,
                correlation=record.correlation,
                channel=record.channel,
            )
        if record.crypto is not None and record.crypto_wire is not None:
            envelope = apply_ws_crypto_metadata(envelope, record.crypto_wire, crypto_outputs)
        prepared = PreparedMessage(
            envelope,
            semantic_payload,
            record.correlation,
            record.channel,
            self._generation,
        )
        protected, semantic_transforms = await self._apply_outbound_transforms(
            prepared,
            operation=record.discriminator,
        )
        encoded = self.protocol.codec.encode(protected.envelope)
        if record.crypto is not None and record.crypto_wire is not None:
            encoded = await protect_ws_frame(
                encoded,
                record.crypto,
                record.crypto_wire,
                context=await self._crypto_context(
                    record.crypto,
                    record.discriminator,
                    record.discriminator,
                    record.channel,
                    CryptoDirection.OUTBOUND,
                    CryptoStage.ENCODED,
                    frame_kind=encoded.kind.value,
                ),
            )
        protected_frame = apply_outbound_frame_transforms(
            encoded, self.config.outbound_frame_transforms
        )
        self._last_protection_snapshot = protection_snapshot(
            protected,
            protected_frame,
            semantic=semantic_transforms,
            exact=self.config.outbound_frame_transforms,
            crypto_profile=record.crypto.profile.name if record.crypto is not None else None,
            crypto_stages=_outbound_crypto_stages(
                (record.crypto, record.crypto_wire)
                if record.crypto is not None and record.crypto_wire is not None
                else None
            ),
        )
        frame = frame_to_zapros(protected_frame)
        completion = asyncio.get_running_loop().create_future()
        self._enqueue(_WriteItem(frame, completion))
        try:
            await completion
        except _AttemptDeliveryFailure as failure:
            self._raise_delivery(record.discriminator, failure)

    async def _restore_subscriptions(self, generation: ConnectionGeneration) -> None:
        for record in self._subscription_records():
            policy = record.subscription.resubscribe_policy
            if isinstance(policy, NeverResubscribe):
                continue
            recovery = isinstance(policy, RecoverBySequence | RecoverByToken)
            if record.correlation is not None:
                old_route = (self._protocol_namespace, record.correlation)
                self._subscriptions_by_correlation.pop(old_route, None)
                record.correlation = self._allocate_correlation()
                self._subscriptions_by_correlation[
                    (self._protocol_namespace, record.correlation)
                ] = record
            record.subscription._mark_restoring(generation, recovered=recovery)
            try:
                await evaluate_middleware(
                    self.config.subscription_middleware,
                    self._middleware_context(
                        operation=record.discriminator,
                        channel=record.channel.value if record.channel is not None else None,
                        event="recover" if recovery else "resubscribe",
                        direction=WsDirection.OUTBOUND,
                        generation=generation,
                    ),
                    allow_patch=False,
                )
                await self._send_subscription(record, recovery=recovery)
            except BaseException as exc:
                record.subscription._fail(exc)
                self._remove_subscription(record)
            else:
                record.subscription._mark_active()

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
        await self.connect()
        correlation = self._allocate_correlation()
        generation = self._generation
        key = _PendingKey(generation, self._protocol_namespace, correlation)
        loop = asyncio.get_running_loop()
        pending = _PendingExchange(
            key,
            loop.create_future(),
            replies=replies,
            crypto=crypto[0] if crypto is not None else None,
            crypto_wire=crypto[1] if crypto is not None else None,
        )
        self._pending[key] = pending
        frame = await self._prepare_outbound(
            discriminator,
            payload,
            correlation=correlation,
            channel=None,
            payload_schema=payload_schema,
            crypto=crypto,
            operation_id=operation_id,
        )
        completion = loop.create_future()
        item = _WriteItem(frame, completion, pending)
        try:
            self._enqueue(item)
            await completion
            try:
                if timeout is None:
                    message = await pending.reply
                else:
                    message = await asyncio.wait_for(asyncio.shield(pending.reply), timeout)
            except TimeoutError as exc:
                raise _AttemptDeliveryFailure(
                    WsCallTimeoutError(
                        f"WebSocket call {discriminator!r} timed out after {timeout} seconds"
                    ),
                    may_have_been_sent=pending.send_started,
                ) from exc
            if crypto is not None and crypto[0].inbound_fields:
                message = await unprotect_ws_message(
                    message,
                    crypto[0],
                    crypto[1],
                    context=await self._crypto_context(
                        crypto[0],
                        operation_id,
                        message.discriminator,
                        message.channel,
                        CryptoDirection.INBOUND,
                        CryptoStage.DOCUMENT,
                    ),
                )
            if replies is None:
                return thaw_value(message.payload)
            return selected_value(replies.inspect(message))
        finally:
            self._pending.pop(key, None)
            if not pending.reply.done():
                pending.reply.cancel()
            elif not pending.reply.cancelled():
                pending.reply.exception()

    def _enqueue(self, item: _WriteItem, *, allow_handshaking: bool = False) -> None:
        queue = self._writer_queue
        permitted = {WsSessionState.READY}
        if allow_handshaking:
            permitted.add(WsSessionState.HANDSHAKING)
        if self._state not in permitted or queue is None:
            raise _AttemptDeliveryFailure(
                RuntimeError("WebSocket connection is not ready"),
                may_have_been_sent=False,
            )
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise WsQueueOverflowError(
                f"WebSocket writer queue capacity {self.config.writer_queue_capacity} exceeded"
            ) from exc

    async def _writer_loop(
        self,
        websocket: AsyncBaseWebSocket,
        generation: ConnectionGeneration,
    ) -> None:
        queue = self._writer_queue
        if queue is None:
            return
        try:
            while True:
                item = await queue.get()
                self._active_write = item
                item.started = True
                if item.pending is not None:
                    item.pending.send_started = True
                try:
                    await websocket.send(item.frame)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    failure = _AttemptDeliveryFailure(exc, may_have_been_sent=True)
                    if not item.completion.done():
                        item.completion.set_exception(failure)
                    await self._fail_connection(exc, generation)
                    return
                else:
                    if not item.completion.done():
                        item.completion.set_result(None)
                finally:
                    self._active_write = None
                    queue.task_done()
        except asyncio.CancelledError:
            raise

    async def _heartbeat_loop(self, generation: ConnectionGeneration) -> None:
        interval = self.config.heartbeat_interval
        if interval is None:
            return
        try:
            while self._state is WsSessionState.READY and generation == self._generation:
                await self.config.sleep(interval)
                if self._state is not WsSessionState.READY or generation != self._generation:
                    return
                self._heartbeat_ack.clear()
                completion = asyncio.get_running_loop().create_future()
                self._enqueue(_WriteItem(PingMessage(b""), completion))
                await completion
                try:
                    await asyncio.wait_for(
                        self._heartbeat_ack.wait(),
                        timeout=self.config.heartbeat_timeout,
                    )
                except TimeoutError:
                    await self._fail_connection(
                        TimeoutError("WebSocket heartbeat acknowledgement timed out"),
                        generation,
                    )
                    return
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._fail_connection(exc, generation)

    async def _reader_loop(
        self,
        websocket: AsyncBaseWebSocket,
        generation: ConnectionGeneration,
    ) -> None:
        try:
            while True:
                raw = await websocket.recv()
                frame = frame_from_zapros(raw, limits=self.config.frame_limits)
                if frame.close_code is not None:
                    disposition = self.protocol.classify_close(frame.close_code)
                    error = ConnectionError(
                        f"WebSocket closed with code {frame.close_code}: {frame.close_reason or ''}"
                    )
                    await self._fail_connection(
                        error,
                        generation,
                        fatal=disposition is CloseDisposition.FATAL,
                        reconnect=disposition is CloseDisposition.RECONNECT,
                    )
                    return
                unprotected_frame = apply_inbound_frame_transforms(
                    frame,
                    self.config.inbound_frame_transforms,
                )
                if not isinstance(unprotected_frame, ParsedValue):
                    if isinstance(unprotected_frame, Malformed):
                        await self._dispatch_protocol_error(unprotected_frame.cause)
                    continue
                decoded_frame = unprotected_frame.value
                if self._inbound_crypto is not None and decoded_frame.kind in {
                    FrameKind.TEXT,
                    FrameKind.BINARY,
                }:
                    try:
                        decoded_frame = await unprotect_ws_frame(
                            decoded_frame,
                            self._inbound_crypto[0],
                            self._inbound_crypto[1],
                            context=await self._crypto_context(
                                self._inbound_crypto[0],
                                "connection",
                                None,
                                None,
                                CryptoDirection.INBOUND,
                                CryptoStage.ENCODED,
                                generation=generation,
                                frame_kind=decoded_frame.kind.value,
                            ),
                        )
                    except Exception as exc:
                        await self._dispatch_protocol_error(exc)
                        continue
                inspected = self.protocol.inspect(decoded_frame)
                if isinstance(inspected, ParsedValue):
                    unprotected_message = apply_inbound_message_transforms(
                        inspected.value,
                        self.config.inbound_message_transforms,
                    )
                    if isinstance(unprotected_message, ParsedValue):
                        message = unprotected_message.value
                        try:
                            await evaluate_middleware(
                                self.config.message_middleware,
                                self._middleware_context(
                                    operation=None,
                                    channel=(
                                        message.channel.value
                                        if message.channel is not None
                                        else None
                                    ),
                                    event=message.discriminator,
                                    direction=WsDirection.INBOUND,
                                    generation=generation,
                                ),
                                allow_patch=False,
                            )
                        except Exception as exc:
                            await self._dispatch_protocol_error(exc)
                        else:
                            await self._route_message(message, generation)
                    elif isinstance(unprotected_message, Malformed):
                        await self._dispatch_protocol_error(unprotected_message.cause)
                elif isinstance(inspected, Malformed):
                    await self._dispatch_protocol_error(inspected.cause)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._fail_connection(exc, generation)

    async def _route_message(
        self,
        message: ProtocolMessage,
        generation: ConnectionGeneration,
    ) -> None:
        if message.terminal_error is not None and message.correlation is not None:
            key = _PendingKey(generation, self._protocol_namespace, message.correlation)
            pending = self._pending.pop(key, None)
            if pending is not None and not pending.reply.done():
                pending.reply.set_exception(message.terminal_error)
                return
            subscription = self._subscriptions_by_correlation.get(
                (self._protocol_namespace, message.correlation)
            )
            if subscription is not None:
                subscription.subscription._fail(message.terminal_error)
                self._remove_subscription(subscription)
                return
        if message.kind is InboundMessageKind.REPLY and message.correlation is not None:
            key = _PendingKey(generation, self._protocol_namespace, message.correlation)
            pending = self._pending.pop(key, None)
            if pending is not None and not pending.reply.done():
                pending.reply.set_result(message)
                return
            subscription = self._subscriptions_by_correlation.get(
                (self._protocol_namespace, message.correlation)
            )
            if subscription is not None:
                await self._publish_subscription(subscription, message, generation)
                return
        if message.kind is InboundMessageKind.EVENT and message.channel is not None:
            subscription = self._subscriptions_by_channel.get(
                (self._protocol_namespace, message.channel)
            )
            if subscription is not None:
                await self._publish_subscription(subscription, message, generation)
                return
        if message.kind is InboundMessageKind.CONTROL:
            if message.control is ControlKind.READY:
                self._protocol_ready.set()
                return
            if message.control is ControlKind.PONG:
                self._heartbeat_ack.set()
                return
            if message.control is ControlKind.PING:
                envelope = self.protocol.build_control(ControlKind.PONG, message.payload)
                if envelope is None:
                    self._enqueue_control(PongMessage(b""))
                else:
                    await self._send_protocol_envelope(
                        envelope,
                        message.payload,
                        correlation=None,
                        channel=None,
                        operation="pong",
                        allow_handshaking=self._state is WsSessionState.HANDSHAKING,
                    )
                return
            if message.control is ControlKind.COMPLETE:
                record = None
                if message.channel is not None:
                    record = self._subscriptions_by_channel.get(
                        (self._protocol_namespace, message.channel)
                    )
                elif message.correlation is not None:
                    record = self._subscriptions_by_correlation.get(
                        (self._protocol_namespace, message.correlation)
                    )
                if record is not None:
                    record.subscription._complete()
                    self._remove_subscription(record)
                    return
        self._dispatch_user_message(message)

    async def _publish_subscription(
        self,
        record: _SubscriptionRecord,
        message: ProtocolMessage,
        generation: ConnectionGeneration,
    ) -> None:
        if record.crypto is not None and record.crypto.inbound_fields:
            try:
                message = await unprotect_ws_message(
                    message,
                    record.crypto,
                    record.crypto_wire or WebSocketEncrypted(),
                    context=await self._crypto_context(
                        record.crypto,
                        record.discriminator,
                        message.discriminator,
                        record.channel,
                        CryptoDirection.INBOUND,
                        CryptoStage.DOCUMENT,
                        generation=generation,
                    ),
                )
            except Exception as exc:
                record.subscription._fail(exc)
                self._remove_subscription(record)
                return
        try:
            await evaluate_middleware(
                self.config.subscription_middleware,
                self._middleware_context(
                    operation=record.discriminator,
                    channel=record.channel.value if record.channel is not None else None,
                    event=message.discriminator,
                    direction=WsDirection.INBOUND,
                    generation=generation,
                ),
                allow_patch=False,
            )
        except Exception as exc:
            record.subscription._fail(exc)
            self._remove_subscription(record)
            return
        value: object
        if record.messages is None:
            value = thaw_value(message.payload)
        else:
            try:
                value = selected_value(record.messages.inspect(message))
            except Exception as exc:
                record.subscription._fail(exc)
                self._remove_subscription(record)
                return
        if not record.subscription._publish(value, generation):
            self._remove_subscription(record)

    def _enqueue_control(self, message: Message) -> None:
        completion = asyncio.get_running_loop().create_future()
        try:
            self._enqueue(_WriteItem(message, completion))
        except WsQueueOverflowError, _AttemptDeliveryFailure:
            return

        def consume(future: asyncio.Future[None]) -> None:
            if not future.cancelled():
                future.exception()

        completion.add_done_callback(consume)

    async def _dispatch_protocol_error(self, cause: Exception) -> None:
        if self._on_message is None:
            return
        message = ProtocolMessage(
            InboundMessageKind.MESSAGE,
            None,
            freeze_value({"protocol_error": type(cause).__name__}),
        )
        self._dispatch_user_message(message)

    def _dispatch_user_message(self, message: ProtocolMessage) -> None:
        handler = self._on_message
        if handler is None:
            return

        async def invoke() -> None:
            try:
                result = handler(message)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # User event handling is isolated from the sole reader task.  WS-04 adds
                # observer diagnostics and bounded event/subscription queues.
                return

        task = asyncio.create_task(invoke(), name="eazy_sdk-ws-message-handler")
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    async def _fail_connection(
        self,
        cause: BaseException,
        generation: ConnectionGeneration,
        *,
        fatal: bool = False,
        reconnect: bool = True,
    ) -> None:
        should_reconnect = False
        async with self._failure_lock:
            if generation != self._generation or self._state in {
                WsSessionState.CLOSING,
                WsSessionState.CLOSED,
            }:
                return
            if fatal:
                self._state = WsSessionState.FAILED
            elif reconnect:
                self._state = WsSessionState.RECONNECTING
            else:
                self._state = WsSessionState.IDLE
            self._fail_outstanding(cause)
            should_reconnect = self._disconnect_subscriptions(
                cause,
                fatal=fatal,
                reconnect=reconnect,
            )
            current = asyncio.current_task()
            for task in (self._reader_task, self._writer_task, self._heartbeat_task):
                if task is not None and task is not current and not task.done():
                    task.cancel()
        if should_reconnect:
            self._schedule_reconnect()

    def _disconnect_subscriptions(
        self,
        cause: BaseException,
        *,
        fatal: bool,
        reconnect: bool,
    ) -> bool:
        retained = False
        for record in self._subscription_records():
            policy = record.subscription.resubscribe_policy
            if fatal:
                record.subscription._fail(
                    SubscriptionDisconnectedError(f"fatal WebSocket disconnect: {cause}")
                )
                self._remove_subscription(record)
            elif not reconnect:
                record.subscription._fail(
                    SubscriptionDisconnectedError(f"WebSocket subscription ended: {cause}")
                )
                self._remove_subscription(record)
            elif isinstance(policy, NeverResubscribe):
                record.subscription._fail(
                    SubscriptionDisconnectedError(
                        f"subscription disconnected and resubscribe is disabled: {cause}"
                    )
                )
                self._remove_subscription(record)
            else:
                record.subscription._mark_disconnected()
                retained = True
        if retained and not self.config.reconnect.delays:
            for record in self._subscription_records():
                record.subscription._fail(
                    SubscriptionDisconnectedError("WebSocket reconnect budget is disabled")
                )
                self._remove_subscription(record)
            return False
        return retained

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        task = asyncio.create_task(self._reconnect_loop(), name="eazy_sdk-ws-reconnect")
        self._reconnect_task = task

        def clear(completed: asyncio.Task[None]) -> None:
            if self._reconnect_task is completed:
                self._reconnect_task = None
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(clear)

    async def _reconnect_loop(self) -> None:
        last_error: BaseException | None = None
        for delay in self.config.reconnect.delays:
            await self.config.sleep(delay)
            if self._state in {WsSessionState.CLOSING, WsSessionState.CLOSED}:
                return
            if self._state is WsSessionState.READY:
                return
            try:
                await self.connect()
            except WsConnectError as exc:
                last_error = exc
                continue
            return
        self._state = WsSessionState.FAILED
        error = SubscriptionDisconnectedError(
            f"WebSocket reconnect budget exhausted: {last_error or 'no connection'}"
        )
        for record in self._subscription_records():
            record.subscription._fail(error)
            self._remove_subscription(record)

    async def _prepare_replay(self) -> None:
        if self._state is WsSessionState.FAILED:
            raise WsConnectError("fatal WebSocket failure forbids replay")
        await self.connect()

    async def _teardown_connection(self) -> None:
        tasks = tuple(
            task
            for task in (self._reader_task, self._writer_task, self._heartbeat_task)
            if task is not None and not task.done()
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._reader_task = None
        self._writer_task = None
        self._heartbeat_task = None
        context = self._connection_context
        self._connection_context = None
        self._websocket = None
        self._writer_queue = None
        self._active_write = None
        if context is not None:
            await context.__aexit__(None, None, None)

    async def aclose(self) -> None:
        async with self._connect_lock:
            if self._state is WsSessionState.CLOSED:
                return
            self._state = WsSessionState.CLOSING
            reconnect_task = self._reconnect_task
            self._reconnect_task = None
            if reconnect_task is not None and reconnect_task is not asyncio.current_task():
                reconnect_task.cancel()
                await asyncio.gather(reconnect_task, return_exceptions=True)
            close_error = WsClientClosedError("WebSocket client closed")
            self._fail_outstanding(close_error)
            for record in self._subscription_records():
                record.subscription._fail(SubscriptionDisconnectedError("WebSocket client closed"))
                self._remove_subscription(record)
            if self._websocket is not None:
                await self._websocket.close(CloseCode.NORMAL, "client closing")
            await self._teardown_connection()
            handler_tasks = tuple(self._handler_tasks)
            for task in handler_tasks:
                task.cancel()
            if handler_tasks:
                await asyncio.gather(*handler_tasks, return_exceptions=True)
            self._handler_tasks.clear()
            if self._owns_zapros_client:
                await self._zapros_client.aclose()
            self._state = WsSessionState.CLOSED

    def _subscription_records(self) -> tuple[_SubscriptionRecord, ...]:
        records: list[_SubscriptionRecord] = []
        seen: set[int] = set()
        for record in (
            *self._subscriptions_by_channel.values(),
            *self._subscriptions_by_correlation.values(),
        ):
            if id(record) not in seen:
                seen.add(id(record))
                records.append(record)
        return tuple(records)

    def _remove_subscription(self, record: _SubscriptionRecord) -> None:
        if record.channel is not None:
            self._subscriptions_by_channel.pop(
                (self._protocol_namespace, record.channel),
                None,
            )
        if record.correlation is not None:
            self._subscriptions_by_correlation.pop(
                (self._protocol_namespace, record.correlation),
                None,
            )

    def _fail_outstanding(self, cause: BaseException) -> None:
        active = self._active_write
        if active is not None and not active.completion.done():
            active.completion.set_exception(
                _AttemptDeliveryFailure(cause, may_have_been_sent=active.started)
            )
        queue = self._writer_queue
        if queue is not None:
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not item.completion.done():
                    item.completion.set_exception(
                        _AttemptDeliveryFailure(cause, may_have_been_sent=item.started)
                    )
                queue.task_done()
        for pending in tuple(self._pending.values()):
            if not pending.reply.done():
                pending.reply.set_exception(
                    _AttemptDeliveryFailure(
                        cause,
                        may_have_been_sent=pending.send_started,
                    )
                )
        self._pending.clear()

    def _allocate_correlation(self) -> CorrelationKey:
        self._next_correlation += 1
        return CorrelationKey(str(self._next_correlation))

    @staticmethod
    def _raise_delivery(discriminator: str, failure: _AttemptDeliveryFailure) -> None:
        if failure.may_have_been_sent:
            if isinstance(failure.cause, WsCallTimeoutError):
                raise failure.cause
            raise DeliveryUnknownError(
                f"WebSocket operation {discriminator!r} may have been delivered"
            ) from failure.cause
        raise DeliveryNotSentError(
            f"WebSocket operation {discriminator!r} was not sent"
        ) from failure.cause


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


__all__ = [
    "AsyncWsClient",
    "MessageHandler",
    "WsCallOptions",
    "WsClientConfig",
    "WsConnector",
    "WsSessionState",
]
