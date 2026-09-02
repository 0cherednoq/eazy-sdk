"""Immutable client policy shared by every public transport factory."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from eazy_sdk.auth import Auth
from eazy_sdk.crypto import CryptoRegistry
from eazy_sdk.dependencies import DependencyRegistry
from eazy_sdk.handlers import HandlerProfile
from eazy_sdk.middleware import MiddlewareRegistration
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
from eazy_sdk.protection import (
    BeforeCallPolicy,
    ChallengePolicy,
    ChallengeSolverBindings,
    InstallableProtection,
    NetworkIdentity,
    NetworkIdentityProvider,
    ProtectionBundle,
    ProtectionFlow,
    SolverBindings,
)
from eazy_sdk.ratelimit_runtime import RateLimiter
from eazy_sdk.request import WireProfile

from .base import CallOptions, RetryPolicy
from .executor import ExecutionRuntime, KeyProvider, Observer


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Transport-independent client runtime policy."""

    auth: Auth | None = None
    dependencies: DependencyRegistry | None = None
    operation_protections: tuple[ProtectionFlow[Any], ...] = ()
    before_call_policies: tuple[BeforeCallPolicy[Any, Any], ...] = ()
    challenge_policies: tuple[ChallengePolicy[Any, Any], ...] = ()
    operation_protection_solvers: SolverBindings = field(default_factory=SolverBindings)
    challenge_solvers: ChallengeSolverBindings = field(
        default_factory=ChallengeSolverBindings
    )
    protection_session_identity: object | None = None
    network_identity: NetworkIdentity | NetworkIdentityProvider | None = None
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
        if any(not isinstance(item, ProtectionFlow) for item in self.operation_protections):
            raise TypeError("operation_protections accepts only ProtectionFlow values")
        if any(not isinstance(item, BeforeCallPolicy) for item in self.before_call_policies):
            raise TypeError("before_call_policies contains a malformed policy")
        if any(not isinstance(item, ChallengePolicy) for item in self.challenge_policies):
            raise TypeError("challenge_policies contains a malformed policy")
        _validate_bundle_identities(
            self.operation_protections,
            self.before_call_policies,
            self.challenge_policies,
        )

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
        *protections: ProtectionBundle | InstallableProtection,
    ) -> ClientConfig:
        bundles = tuple(
            item if isinstance(item, ProtectionBundle) else item.to_bundle()
            for item in protections
        )
        operation_protections = self.operation_protections + tuple(
            flow for bundle in bundles for flow in bundle.operation_protections
        )
        before_call_policies = self.before_call_policies + tuple(
            policy for bundle in bundles for policy in bundle.before_call_policies
        )
        challenge_policies = self.challenge_policies + tuple(
            policy for bundle in bundles for policy in bundle.challenge_policies
        )
        _validate_bundle_identities(
            operation_protections,
            before_call_policies,
            challenge_policies,
        )
        operation_solvers = SolverBindings(
            *self.operation_protection_solvers.bindings,
            *(binding for bundle in bundles for binding in bundle.operation_solver_bindings),
        )
        challenge_solvers = ChallengeSolverBindings(
            *self.challenge_solvers.bindings,
            *(binding for bundle in bundles for binding in bundle.challenge_solver_bindings),
        )
        return replace(
            self,
            operation_protections=operation_protections,
            before_call_policies=before_call_policies,
            challenge_policies=challenge_policies,
            operation_protection_solvers=operation_solvers,
            challenge_solvers=challenge_solvers,
        )


def _validate_bundle_identities(
    flows: tuple[ProtectionFlow[Any], ...],
    before: tuple[BeforeCallPolicy[Any, Any], ...],
    challenge: tuple[ChallengePolicy[Any, Any], ...],
) -> None:
    requirements = [id(flow.requirement) for flow in flows]
    if len(requirements) != len(set(requirements)):
        raise ValueError("duplicate operation protection requirement")
    identities = [policy.identity for policy in before]
    identities.extend(policy.identity for policy in challenge)
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise ValueError("duplicate protection policy identity: " + ", ".join(duplicates))


def _runtime_from_boundary(
    profile: HandlerProfile,
    send: Any,
    *,
    base_url: str,
    config: ClientConfig,
    allow_async_crypto: bool,
    network_identity: NetworkIdentity | NetworkIdentityProvider | None = None,
) -> ExecutionRuntime:
    from eazy_sdk.auth.core import AuthProviders
    from eazy_sdk.dependencies import DependencyRegistry
    return ExecutionRuntime(
        handler_profile=profile,
        send=send,
        base_url=base_url,
        dependencies=config.dependencies or DependencyRegistry(),
        auth=(config.auth._runtime_providers() if config.auth is not None else AuthProviders()),
        operation_protections=config.operation_protections,
        before_call_policies=config.before_call_policies,
        challenge_policies=config.challenge_policies,
        operation_protection_solvers=config.operation_protection_solvers,
        challenge_solvers=config.challenge_solvers,
        protection_session_identity=config.protection_session_identity,
        network_identity=(
            network_identity if network_identity is not None else config.network_identity
        ),
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
