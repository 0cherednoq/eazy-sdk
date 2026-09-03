"""Failure handling, reconnection and subscription restore/replay."""

from __future__ import annotations

import asyncio

from ._artifacts import (
    ConnectionGeneration,
)
from ._client_state import (
    WsSessionState,
    _AttemptDeliveryFailure,
    _WsClientBase,
)
from ._runtime_stages import (
    DisconnectAction,
    FailureState,
    ReconnectAction,
    disconnect_action,
    failure_state,
    reconnect_action,
)
from .errors import (
    SubscriptionDisconnectedError,
    WsConnectError,
)
from .middleware import (
    WsDirection,
    evaluate_middleware,
)
from .policies import (
    NeverResubscribe,
    RecoverBySequence,
    RecoverByToken,
)


class _ReconnectMixin(_WsClientBase):
    """Failure handling, reconnection and subscription restore/replay."""

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
            target = failure_state(fatal=fatal, reconnect=reconnect)
            self._state = {
                FailureState.FAILED: WsSessionState.FAILED,
                FailureState.RECONNECTING: WsSessionState.RECONNECTING,
                FailureState.IDLE: WsSessionState.IDLE,
            }[target]
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
            action = disconnect_action(policy, fatal=fatal, reconnect=reconnect)
            if action is DisconnectAction.FAIL_FATAL:
                record.subscription._fail(
                    SubscriptionDisconnectedError(f"fatal WebSocket disconnect: {cause}")
                )
                self._remove_subscription(record)
            elif action is DisconnectAction.FAIL_ENDED:
                record.subscription._fail(
                    SubscriptionDisconnectedError(f"WebSocket subscription ended: {cause}")
                )
                self._remove_subscription(record)
            elif action is DisconnectAction.FAIL_DISABLED:
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
            if reconnect_action(self._state.value) is ReconnectAction.STOP:
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
