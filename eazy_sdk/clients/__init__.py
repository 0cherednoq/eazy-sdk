"""Public client policy and lazily loaded Zapros client surfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import (
    AttemptLimitExceeded,
    CallOptions,
    RedirectLimitExceeded,
    RetryPolicy,
    UnsafeReplayError,
)
from .config import ClientConfig

if TYPE_CHECKING:
    from .zapros_client import AsyncClient, Client


def __getattr__(name: str) -> Any:
    if name not in {"AsyncClient", "Client"}:
        raise AttributeError(name)
    from .zapros_client import AsyncClient, Client

    value = AsyncClient if name == "AsyncClient" else Client
    globals()[name] = value
    return value


__all__ = [
    "AsyncClient",
    "AttemptLimitExceeded",
    "CallOptions",
    "Client",
    "ClientConfig",
    "RedirectLimitExceeded",
    "RetryPolicy",
    "UnsafeReplayError",
]
