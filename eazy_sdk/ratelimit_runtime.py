"""Rate-limit effect used once for every imminent transport emit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from eazy_sdk.core import (
    OperationIdentity,
)


@dataclass(frozen=True, slots=True)
class RateLimitContext:
    operation: OperationIdentity
    method: str
    authority: str
    attempt: int
    attempt_kind: str


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    delay: float = 0.0


class RateLimiter(Protocol):
    def reserve(self, context: RateLimitContext) -> RateLimitDecision: ...
