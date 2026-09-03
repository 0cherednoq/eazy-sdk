"""Public client configuration shared by both effect runners."""

from __future__ import annotations

from eazy_sdk.core.errors import EazySdkError
from eazy_sdk.policies import CallOptions, RetryPolicy


class EventLoopConflictError(EazySdkError, RuntimeError):
    """A synchronous client was used from a thread that already runs an event loop."""


class AttemptLimitError(EazySdkError):
    pass


class RedirectLimitError(EazySdkError):
    pass


class UnsafeReplayError(EazySdkError):
    pass


__all__ = [
    "AttemptLimitError",
    "CallOptions",
    "EventLoopConflictError",
    "RedirectLimitError",
    "RetryPolicy",
    "UnsafeReplayError",
]
