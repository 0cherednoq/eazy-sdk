"""Transport-neutral call and retry policies shared by clients and offline preparation."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from eazy_sdk.handlers import EmitOptions
from eazy_sdk.middleware import MiddlewareRegistration

type AsyncSleep = Callable[[float], Awaitable[None]]
type RandomSource = Callable[[], float]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy lowered into the shared attempt coordinator."""

    max_attempts: int
    retry_statuses: frozenset[int]
    base_delay: float = 0.0
    max_delay: float = 0.0
    jitter: float = 0.0
    _sleep: AsyncSleep = dataclass_field(default=asyncio.sleep, repr=False, compare=False)
    _random: RandomSource = dataclass_field(default=random.random, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.base_delay < 0 or self.max_delay < 0 or self.jitter < 0:
            raise ValueError("retry delays and jitter cannot be negative")
        if self.max_delay and self.base_delay > self.max_delay:
            raise ValueError("base_delay cannot exceed max_delay")

    @classmethod
    def none(cls) -> RetryPolicy:
        return cls(1, frozenset())

    @classmethod
    def safe(
        cls,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.0,
        max_delay: float = 30.0,
        jitter: float = 0.0,
        retry_statuses: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504}),
        sleep: AsyncSleep = asyncio.sleep,
        random_source: RandomSource = random.random,
    ) -> RetryPolicy:
        return cls(
            max_attempts,
            retry_statuses,
            base_delay,
            max_delay,
            jitter,
            sleep,
            random_source,
        )

    @property
    def retries(self) -> int:
        return self.max_attempts - 1

    async def wait(self, retry_number: int) -> None:
        if retry_number < 1:
            raise ValueError("retry_number must be at least one")
        delay = self.base_delay * (2 ** (retry_number - 1))
        if self.max_delay:
            delay = min(delay, self.max_delay)
        if self.jitter:
            delay += self._random() * self.jitter
        if delay > 0:
            await self._sleep(delay)


@dataclass(frozen=True, slots=True)
class CallOptions:
    timeout: float | None = None
    max_attempts: int = 1
    transport_retries: int = 0
    auth_retries: int = 0
    max_redirects: int = 0
    middleware: tuple[MiddlewareRegistration, ...] = ()
    retry: RetryPolicy = dataclass_field(default_factory=RetryPolicy.none)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.transport_retries < 0 or self.auth_retries < 0 or self.max_redirects < 0:
            raise ValueError("retry budgets must not be negative")

    def emit_options(self) -> EmitOptions:
        return EmitOptions(timeout=self.timeout)


__all__ = ["CallOptions", "RetryPolicy"]
