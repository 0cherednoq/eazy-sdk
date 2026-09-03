"""Immutable client policy shared by every public transport factory."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from eazy_sdk.auth import Auth
from eazy_sdk.crypto import CryptoRegistry
from eazy_sdk.dependencies import DependencyRegistry
from eazy_sdk.handlers import HandlerProfile
from eazy_sdk.middleware import MiddlewareRegistration
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
from eazy_sdk.protection.advanced import (
    InstallableProtection,
    ProtectionBundle,
    ProtectionConfigurationError,
)
from eazy_sdk.ratelimit_runtime import RateLimiter
from eazy_sdk.request import WireProfile

from .base import CallOptions, RetryPolicy
from .executor import ExecutionRuntime, KeyProvider, Observer


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Transport-independent client runtime policy.

    Protection is one group: pass installable guards through ``guards=`` (lowered at
    construction) or a complete ``ProtectionBundle`` through ``protection=``; ``retry=`` groups
    the retry policy. ``with_protection()`` returns a copy with more guards installed.
    """

    auth: Auth | None = None
    dependencies: DependencyRegistry | None = None
    protection: ProtectionBundle | None = None
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
    guards: Sequence[InstallableProtection] = ()
    """Installable guards lowered at construction; equivalent to ``with_protection(*guards)``."""

    def __post_init__(self) -> None:
        if self.auth_retries < 0 or self.max_redirects < 0:
            raise ValueError("client retry budgets cannot be negative")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.protection is not None and not isinstance(self.protection, ProtectionBundle):
            raise TypeError("protection must be a ProtectionBundle")
        if self.guards:
            guards = tuple(self.guards)
            object.__setattr__(self, "guards", ())
            object.__setattr__(self, "protection", self.with_protection(*guards).protection)

    @property
    def bundle(self) -> ProtectionBundle:
        """The installed protection, empty when nothing is configured."""

        return self.protection if self.protection is not None else ProtectionBundle()

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

    def with_protection(
        self,
        *protections: InstallableProtection,
    ) -> ClientConfig:
        """Return a copy with ``protections`` lowered and merged into ``protection``."""

        bundles: list[ProtectionBundle] = []
        for item in protections:
            if not isinstance(item, InstallableProtection):
                raise ProtectionConfigurationError(
                    "with_protection accepts only installable protection descriptors"
                )
            bundle = item.to_bundle()
            if not isinstance(bundle, ProtectionBundle):
                raise ProtectionConfigurationError(
                    "installable protection returned a malformed lowering bundle"
                )
            if not bundle:
                raise ProtectionConfigurationError(
                    "installable protection lowered to an empty bundle"
                )
            bundles.append(bundle)
        merged = self.bundle.merge(*bundles)
        return replace(self, protection=merged if merged else None)


def _runtime_from_boundary(
    profile: HandlerProfile,
    send: Any,
    *,
    base_url: str,
    config: ClientConfig,
    allow_async_crypto: bool,
    protection_session_owner: object | None = None,
) -> ExecutionRuntime:
    from eazy_sdk.auth.core import AuthProviders
    from eazy_sdk.dependencies import DependencyRegistry
    return ExecutionRuntime(
        handler_profile=profile,
        send=send,
        base_url=base_url,
        dependencies=config.dependencies or DependencyRegistry(),
        auth=(config.auth._runtime_providers() if config.auth is not None else AuthProviders()),
        operation_protections=config.bundle.operation_protections,
        before_call_policies=config.bundle.before_call_policies,
        challenge_policies=config.bundle.challenge_policies,
        solver_bindings=config.bundle.solvers,
        protection_session_owner=protection_session_owner,
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
