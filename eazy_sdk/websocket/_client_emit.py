"""Outbound path: send/call/subscribe, envelopes, transforms and the write queue."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import cast

from zapros.websocket import (
    Message,
)

from eazy_sdk.crypto import (
    CryptoDirection,
    CryptoOutputValue,
    CryptoStage,
    PayloadCrypto,
    WebSocketCryptoContext,
    WebSocketEncrypted,
)
from eazy_sdk.crypto._runtime import CompiledPayloadCrypto

from ._artifacts import (
    ChannelKey,
    CorrelationKey,
    FrozenValue,
    PreparedMessage,
    WsOperationKind,
    thaw_value,
)
from ._client_state import (
    WsCallOptions,
    WsSessionState,
    _AttemptDeliveryFailure,
    _message_models,
    _outbound_crypto_stages,
    _PendingExchange,
    _PendingKey,
    _reply_models,
    _ResolvedMessageAuth,
    _SubscriptionRecord,
    _WriteItem,
    _WsClientBase,
)
from ._crypto import (
    apply_ws_crypto_metadata,
    protect_ws_document,
    unprotect_ws_message,
)
from ._runtime_stages import (
    RecoveryMode,
    WritePreparation,
    prepare_write,
    recovery_decision,
    write_admitted,
)
from .errors import (
    DeliveryNotSentError,
    DeliveryUnknownError,
    SubscriptionDisconnectedError,
    WsCallTimeoutError,
    WsQueueOverflowError,
)
from .middleware import (
    WsDirection,
    apply_message_patch,
    evaluate_middleware,
)
from .policies import (
    NeverReplay,
    NeverResubscribe,
    OverflowPolicy,
    ResubscribePolicy,
    WsReplayPolicy,
    replay_allowed,
)
from .protection import (
    OutboundMessageTransform,
    apply_outbound_message_transforms,
)
from .schemas import (
    JsonPayload,
    Messages,
    OutboundPayload,
    Replies,
    selected_value,
)
from .subscriptions import Subscription


class _EmitMixin(_WsClientBase):
    """Outbound path: send/call/subscribe, envelopes, transforms and the write queue."""

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
        crypto_context = None
        if crypto is not None:
            compiled_crypto = crypto[0]

            def prepare_crypto_context(frame_kind: str) -> Awaitable[WebSocketCryptoContext]:
                return self._crypto_context(
                    compiled_crypto,
                    operation_id or operation,
                    operation,
                    channel,
                    CryptoDirection.OUTBOUND,
                    CryptoStage.ENCODED,
                    frame_kind=frame_kind,
                )

            crypto_context = prepare_crypto_context
        result = await prepare_write(
            WritePreparation(
                envelope=envelope,
                payload=semantic_payload,
                correlation=correlation,
                channel=channel,
                generation=self._generation,
                codec=self.protocol.codec,
                exact_transforms=self.config.outbound_frame_transforms,
                crypto=crypto,
                crypto_context=crypto_context,
                crypto_stages=_outbound_crypto_stages(crypto),
            ),
            prepare_semantic=lambda prepared: self._apply_outbound_transforms(
                prepared,
                operation=operation,
            ),
        )
        self._last_protection_snapshot = result.snapshot
        return result.frame

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
        recovery_plan = recovery_decision(
            requested=recovery,
            channel=record.channel,
            token=record.subscription._recovery_token() if recovery else None,
        )
        if recovery_plan.error is not None:
            raise SubscriptionDisconnectedError(recovery_plan.error)
        envelope: FrozenValue
        if recovery_plan.mode is RecoveryMode.RECOVER:
            assert record.channel is not None
            assert recovery_plan.token is not None
            built = self.protocol.build_recovery(record.channel, recovery_plan.token)
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
        frame = await self._prepare_envelope(
            envelope,
            semantic_payload,
            correlation=record.correlation,
            channel=record.channel,
            operation=record.discriminator,
            crypto=(record.crypto, record.crypto_wire)
            if record.crypto is not None and record.crypto_wire is not None
            else None,
            operation_id=record.discriminator,
        )
        completion = asyncio.get_running_loop().create_future()
        self._enqueue(_WriteItem(frame, completion))
        try:
            await completion
        except _AttemptDeliveryFailure as failure:
            self._raise_delivery(record.discriminator, failure)

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
        if not write_admitted(
            self._state.value,
            allow_handshaking=allow_handshaking,
            queue_present=queue is not None,
        ):
            raise _AttemptDeliveryFailure(
                RuntimeError("WebSocket connection is not ready"),
                may_have_been_sent=False,
            )
        assert queue is not None
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise WsQueueOverflowError(
                f"WebSocket writer queue capacity {self.config.writer_queue_capacity} exceeded"
            ) from exc

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
