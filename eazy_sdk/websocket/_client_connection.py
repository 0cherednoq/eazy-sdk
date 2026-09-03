"""Connection lifecycle: connect, reader/writer/heartbeat loops, dispatch, teardown."""

from __future__ import annotations

import asyncio
import inspect
from typing import Self

from zapros.websocket import (
    AsyncBaseWebSocket,
    CloseCode,
    Message,
    PingMessage,
    PongMessage,
)

from eazy_sdk.core.kernel import Malformed, ParsedValue
from eazy_sdk.crypto import (
    CryptoDirection,
    CryptoStage,
    WebSocketEncrypted,
)

from ._artifacts import (
    ConnectionGeneration,
    FrameKind,
    freeze_value,
    thaw_value,
)
from ._client_state import (
    WsSessionState,
    _AttemptDeliveryFailure,
    _PendingKey,
    _SubscriptionRecord,
    _WriteItem,
    _WsClientBase,
)
from ._crypto import (
    unprotect_ws_frame,
    unprotect_ws_message,
)
from ._runtime_stages import (
    ConnectAction,
    ReaderRoute,
    connect_action,
    route_reader_message,
)
from .errors import (
    SubscriptionDisconnectedError,
    WsClientClosedError,
    WsConnectError,
    WsQueueOverflowError,
)
from .frames import frame_from_zapros
from .middleware import (
    WsDirection,
    WsMiddlewareContext,
    evaluate_middleware,
)
from .protection import (
    apply_inbound_frame_transforms,
    apply_inbound_message_transforms,
)
from .protocols import (
    CloseDisposition,
    ControlKind,
    InboundMessageKind,
    ProtocolMessage,
)
from .schemas import (
    selected_value,
)


class _ConnectionMixin(_WsClientBase):
    """Connection lifecycle: connect, reader/writer/heartbeat loops, dispatch, teardown."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def connect(self) -> None:
        restore = False
        async with self._connect_lock:
            admission = connect_action(self._state.value)
            if admission is ConnectAction.RETURN_READY:
                return
            if admission is ConnectAction.REJECT_CLOSED:
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
        key = (
            _PendingKey(generation, self._protocol_namespace, message.correlation)
            if message.correlation is not None
            else None
        )
        pending = self._pending.get(key) if key is not None else None
        correlation_subscription = (
            self._subscriptions_by_correlation.get(
                (self._protocol_namespace, message.correlation)
            )
            if message.correlation is not None
            else None
        )
        channel_subscription = (
            self._subscriptions_by_channel.get((self._protocol_namespace, message.channel))
            if message.channel is not None
            else None
        )
        complete_subscription = (
            channel_subscription if message.channel is not None else correlation_subscription
        )
        decision = route_reader_message(
            message,
            pending_present=pending is not None,
            pending_open=pending is not None and not pending.reply.done(),
            correlation_subscription=correlation_subscription is not None,
            channel_subscription=channel_subscription is not None,
            complete_subscription=complete_subscription is not None,
        )
        if decision.discard_pending and key is not None:
            self._pending.pop(key, None)
        if decision.route is ReaderRoute.PENDING_ERROR:
            assert pending is not None and message.terminal_error is not None
            pending.reply.set_exception(message.terminal_error)
            return
        if decision.route is ReaderRoute.SUBSCRIPTION_ERROR:
            assert correlation_subscription is not None and message.terminal_error is not None
            correlation_subscription.subscription._fail(message.terminal_error)
            self._remove_subscription(correlation_subscription)
            return
        if decision.route is ReaderRoute.PENDING_REPLY:
            assert pending is not None
            pending.reply.set_result(message)
            return
        if decision.route is ReaderRoute.SUBSCRIPTION_MESSAGE:
            subscription = (
                correlation_subscription
                if message.kind is InboundMessageKind.REPLY
                else channel_subscription
            )
            assert subscription is not None
            await self._publish_subscription(subscription, message, generation)
            return
        if decision.route is ReaderRoute.READY:
            self._protocol_ready.set()
            return
        if decision.route is ReaderRoute.PONG:
            self._heartbeat_ack.set()
            return
        if decision.route is ReaderRoute.PING:
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
        if decision.route is ReaderRoute.SUBSCRIPTION_COMPLETE:
            assert complete_subscription is not None
            complete_subscription.subscription._complete()
            self._remove_subscription(complete_subscription)
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
        except (WsQueueOverflowError, _AttemptDeliveryFailure):
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
