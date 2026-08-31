"""Immutable client policy shared by every public transport factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eazy_sdk.auth import Auth
from eazy_sdk.crypto import CryptoRegistry
from eazy_sdk.dependencies import DependencyRegistry
from eazy_sdk.handlers import HandlerProfile
from eazy_sdk.middleware import MiddlewareRegistration
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
from eazy_sdk.protection import SolverBindings, SolverRegistry
from eazy_sdk.ratelimit_runtime import RateLimiter
from eazy_sdk.request import WireProfile

from .base import CallOptions, RetryPolicy
from .executor import ExecutionRuntime, KeyProvider, Observer


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Transport-independent client runtime policy."""

    auth: Auth | None = None
    dependencies: DependencyRegistry | None = None
    solvers: SolverRegistry | None = None
    protection_solvers: SolverBindings = field(default_factory=SolverBindings)
    signals: tuple[object, ...] = ()
    protections: tuple[object, ...] = ()
    middleware: tuple[MiddlewareRegistration, ...] = ()
    rate_limiter: RateLimiter | None = None
    key_provider: KeyProvider | None = None
    observer: Observer | None = None
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters)
    profile: WireProfile | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy.none)
    auth_retries: int = 1
    max_redirects: int = 0
    timeout: float | None = None
    crypto: CryptoRegistry | None = None

    def __post_init__(self) -> None:
        if self.auth_retries < 0 or self.max_redirects < 0:
            raise ValueError("client retry budgets cannot be negative")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")

    def call_options(self) -> CallOptions:
        retry_replays = self.retry.retries
        return CallOptions(
            timeout=self.timeout,
            max_attempts=1 + retry_replays + self.auth_retries + self.max_redirects,
            transport_retries=retry_replays,
            auth_retries=self.auth_retries,
            max_redirects=self.max_redirects,
            retry=self.retry,
        )


def _runtime_from_boundary(
    profile: HandlerProfile,
    send: Any,
    *,
    base_url: str,
    config: ClientConfig,
    allow_async_crypto: bool,
) -> ExecutionRuntime:
    from eazy_sdk.auth.core import AuthProviders
    from eazy_sdk.dependencies import DependencyRegistry
    from eazy_sdk.protection import SolverRegistry

    return ExecutionRuntime(
        handler_profile=profile,
        send=send,
        base_url=base_url,
        dependencies=config.dependencies or DependencyRegistry(),
        auth=(config.auth._runtime_providers() if config.auth is not None else AuthProviders()),
        solvers=config.solvers or SolverRegistry(),
        protection_solvers=config.protection_solvers,
        signals=config.signals,
        protections=config.protections,
        middleware=config.middleware,
        limiter=config.rate_limiter,
        key_provider=config.key_provider,
        observer=config.observer,
        models=config.models,
        profile=config.profile,
        crypto=config.crypto or CryptoRegistry(),
        allow_async_crypto=allow_async_crypto,
    )


__all__ = ["ClientConfig"]
