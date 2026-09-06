"""Public client policy and lazily loaded Zapros client surfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eazy_sdk.driver import SynchronousSuspensionError

from .base import (
    AttemptLimitError,
    CallOptions,
    RedirectLimitError,
    RetryPolicy,
    UnsafeReplayError,
)
from .config import ClientConfig, Hooks, Resilience, Security

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
    "AttemptLimitError",
    "CallOptions",
    "Client",
    "ClientConfig",
    "Hooks",
    "RedirectLimitError",
    "Resilience",
    "RetryPolicy",
    "Security",
    "SynchronousSuspensionError",
    "UnsafeReplayError",
]
