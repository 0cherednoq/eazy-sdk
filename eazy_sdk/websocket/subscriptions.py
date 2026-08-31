"""Bounded WebSocket subscription values and recovery metadata."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import cast

from ._artifacts import ConnectionGeneration, FrozenValue, freeze_value
from .errors import RecoveryGapError, SubscriptionOverflowError
from .policies import (
    OverflowPolicy,
    RecoverBySequence,
    RecoverByToken,
    ResubscribeFromStart,
    ResubscribePolicy,
)


@dataclass(frozen=True, slots=True)
class Event[T]:
    value: T
    generation: ConnectionGeneration
    recovered: bool
    last_position: object | None = None
    gap: bool = False


class SubscriptionState(Enum):
    ACTIVE = "active"
    DISCONNECTED = "disconnected"
    RESUBSCRIBING = "resubscribing"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class _Terminal:
    error: BaseException | None = None


type _QueueItem[T] = Event[T] | _Terminal


class Subscription[T]:
    """A bounded async iterator that never performs socket I/O itself."""

    def __init__(
        self,
        *,
        close: Callable[[], Awaitable[None]],
        resubscribe: ResubscribePolicy,
        queue_capacity: int,
        overflow: OverflowPolicy,
        generation: ConnectionGeneration,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("subscription queue capacity must be positive")
        self._close_callback = close
        self._resubscribe = resubscribe
        self._capacity = queue_capacity
        self._overflow = overflow
        self._queue: asyncio.Queue[_QueueItem[T]] = asyncio.Queue(maxsize=queue_capacity)
        self._state = SubscriptionState.ACTIVE
        self._generation = generation
        self._recovered_generation: ConnectionGeneration | None = None
        self._last_position: object | None = None
        self._gap_pending = False
        self._closed = False

    @property
    def state(self) -> SubscriptionState:
        return self._state

    @property
    def resubscribe_policy(self) -> ResubscribePolicy:
        return self._resubscribe

    @property
    def last_position(self) -> object | None:
        return self._last_position

    def __aiter__(self) -> Subscription[T]:
        return self

    async def __anext__(self) -> Event[T]:
        item = await self._queue.get()
        self._queue.task_done()
        if isinstance(item, _Terminal):
            if item.error is not None:
                raise item.error
            raise StopAsyncIteration
        return item

    async def __aenter__(self) -> Subscription[T]:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._state = SubscriptionState.CANCELLED
        self._put_terminal(_Terminal())
        await self._close_callback()

    def _publish(self, value: object, generation: ConnectionGeneration) -> bool:
        if self._state not in {
            SubscriptionState.ACTIVE,
            SubscriptionState.RESUBSCRIBING,
            SubscriptionState.RECOVERING,
        }:
            return False
        try:
            position = self._extract_position(value)
        except (TypeError, ValueError) as exc:
            self._fail(exc)
            return False
        if isinstance(self._resubscribe, RecoverBySequence) and position is not None:
            previous = self._last_position
            if isinstance(previous, int) and isinstance(position, int) and position > previous + 1:
                self._fail(
                    RecoveryGapError(
                        expected=previous + 1,
                        received=position,
                        generation=generation.value,
                    )
                )
                return False
        if position is not None:
            self._last_position = position
        event = Event(
            cast(T, value),
            generation,
            recovered=self._recovered_generation == generation,
            last_position=self._last_position,
            gap=self._gap_pending,
        )
        if not self._queue.full():
            self._queue.put_nowait(event)
            self._gap_pending = False
            return True
        if self._overflow is OverflowPolicy.DROP_NEWEST:
            self._gap_pending = True
            return True
        if self._overflow is OverflowPolicy.DROP_OLDEST:
            self._queue.get_nowait()
            self._queue.task_done()
            self._queue.put_nowait(replace(event, gap=True))
            self._gap_pending = False
            return True
        self._fail(
            SubscriptionOverflowError(f"subscription queue capacity {self._capacity} exceeded")
        )
        return False

    def _mark_disconnected(self) -> None:
        if not self._closed:
            self._state = SubscriptionState.DISCONNECTED

    def _mark_restoring(self, generation: ConnectionGeneration, *, recovered: bool) -> None:
        self._generation = generation
        if recovered:
            self._state = SubscriptionState.RECOVERING
            self._recovered_generation = generation
        else:
            self._state = SubscriptionState.RESUBSCRIBING
            self._recovered_generation = None
            if isinstance(self._resubscribe, ResubscribeFromStart):
                self._last_position = None

    def _mark_active(self) -> None:
        if not self._closed:
            self._state = SubscriptionState.ACTIVE

    def _recovery_token(self) -> FrozenValue | None:
        if self._last_position is None:
            return None
        return freeze_value(self._last_position)

    def _fail(self, error: BaseException) -> None:
        if self._state in {
            SubscriptionState.COMPLETED,
            SubscriptionState.FAILED,
            SubscriptionState.CANCELLED,
        }:
            return
        self._state = SubscriptionState.FAILED
        self._closed = True
        self._put_terminal(_Terminal(error))

    def _complete(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._state = SubscriptionState.COMPLETED
        self._put_terminal(_Terminal())

    def _extract_position(self, value: object) -> object | None:
        field: str | None = None
        if isinstance(self._resubscribe, RecoverBySequence):
            field = self._resubscribe.position_field
        elif isinstance(self._resubscribe, RecoverByToken):
            field = self._resubscribe.token_field
        if field is None:
            return None
        if not isinstance(value, dict):
            raise TypeError(f"recovery payload must be an object containing {field!r}")
        if field not in value:
            raise ValueError(f"recovery payload has no {field!r} field")
        position = value[field]
        if isinstance(self._resubscribe, RecoverBySequence) and not isinstance(position, int):
            raise TypeError(f"sequence field {field!r} must be an integer")
        return cast(object, position)

    def _put_terminal(self, terminal: _Terminal) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
        self._queue.put_nowait(terminal)


__all__ = ["Event", "Subscription", "SubscriptionState"]
