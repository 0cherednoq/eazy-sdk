"""Immutable client policy, grouped by owner.

A client delivers bytes. What survives here is transport policy only: how hard to try
(:class:`Resilience`), what guards the host puts in front of it (:class:`Security`) and what
observes the exchange (:class:`Hooks`). Credentials, signing keys and dependencies belong to
:class:`~eazy_sdk.identity.Identity`; the model library and encoding backend belong to
:class:`~eazy_sdk.serialization.Serialization` on the SDK root; signatures and payload-crypto
profiles are contract, declared on the operation or the router.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from eazy_sdk.crypto import CryptoRegistry
from eazy_sdk.handlers import HandlerProfile
from eazy_sdk.middleware import MiddlewareRegistration
from eazy_sdk.protection.advanced import (
    InstallableProtection,
    ProtectionBundle,
    ProtectionConfigurationError,
)
from eazy_sdk.ratelimit_runtime import RateLimiter
from eazy_sdk.response._mapping import ErrorsSpec

from .base import CallOptions, RetryPolicy
from .executor import ExecutionRuntime


@dataclass(frozen=True, slots=True)
class Resilience:
    """How hard one call tries: retry, auth replay, redirects, timeout, rate limiting."""

    retry: RetryPolicy = field(default_factory=RetryPolicy.none)
    auth_retries: int = 1
    max_redirects: int = 0
    timeout: float | None = None
    rate_limiter: RateLimiter | None = None

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


@dataclass(frozen=True, slots=True)
class Security:
    """Guard and anti-bot policy: clearance is bound to the host, session and IP."""

    protection: ProtectionBundle = field(default_factory=ProtectionBundle)

    def __post_init__(self) -> None:
        if not isinstance(self.protection, ProtectionBundle):
            raise TypeError("protection must be a ProtectionBundle")

    @classmethod
    def of(cls, *guards: InstallableProtection) -> Security:
        """Lower installable guards into one bundle, in the factory rather than after it."""

        return cls().with_protection(*guards)

    def with_protection(self, *protections: InstallableProtection) -> Security:
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
        return Security(self.protection.merge(*bundles))


@dataclass(frozen=True, slots=True)
class Hooks:
    """What observes and wraps the exchange without deciding its content."""

    middleware: tuple[MiddlewareRegistration, ...] = ()


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Transport policy in three groups, plus the host-scoped crypto rules of phase 48.

    ``crypto`` holds the host/path-scoped payload-crypto registry. It is the one field still
    waiting for a decision (phase 48.4): declaring crypto by address is a second way to say
    what the operation and the router already say by place.
    """

    resilience: Resilience = field(default_factory=Resilience)
    security: Security = field(default_factory=Security)
    hooks: Hooks = field(default_factory=Hooks)
    crypto: CryptoRegistry | None = None
    errors: Mapping[str, ErrorsSpec] = field(default_factory=dict)
    """Error cases a host answers with, whichever SDK is speaking to it:
    ``{"books.example": {429: RateLimited, 503: AccessBlocked}}``.

    The key is the exact host, without a port and case-insensitive. These cases are the
    outermost layer: an operation and its service both win a tie against them.
    """

    def __post_init__(self) -> None:
        if not isinstance(self.errors, Mapping):
            raise TypeError("ClientConfig.errors maps a host to its error cases")
        for host in self.errors:
            if not isinstance(host, str) or not host or ":" in host:
                raise TypeError(
                    f"ClientConfig.errors key {host!r} must be a host without a port"
                )

    @property
    def bundle(self) -> ProtectionBundle:
        """The installed protection, empty when nothing is configured."""

        return self.security.protection

    def call_options(self) -> CallOptions:
        return self.resilience.call_options()

    def with_protection(self, *protections: InstallableProtection) -> ClientConfig:
        """Return a copy with ``protections`` lowered and merged into ``security``."""

        return replace(self, security=self.security.with_protection(*protections))


def _runtime_from_boundary(
    profile: HandlerProfile,
    send: Any,
    *,
    base_url: str,
    config: ClientConfig,
    allow_async_crypto: bool,
    protection_session_owner: object | None = None,
) -> ExecutionRuntime:
    bundle = config.security.protection
    return ExecutionRuntime(
        handler_profile=profile,
        send=send,
        base_url=base_url,
        operation_protections=bundle.operation_protections,
        before_call_policies=bundle.before_call_policies,
        challenge_policies=bundle.challenge_policies,
        solver_bindings=bundle.solvers,
        protection_session_owner=protection_session_owner,
        middleware=config.hooks.middleware,
        limiter=config.resilience.rate_limiter,
        crypto=config.crypto or CryptoRegistry(),
        errors=config.errors,
        allow_async_crypto=allow_async_crypto,
    )


__all__ = ["ClientConfig", "Hooks", "Resilience", "Security"]
