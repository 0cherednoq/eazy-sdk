"""Identity: the session scope that owns credentials, signing keys and dependencies.

A client carries bytes: its cookie jar, connection pool, proxy and guard session belong to
the transport. Who the caller is does not: credentials, the session store behind them, the
signing keys and the dependency registry belong to an :class:`Identity`. One identity can
serve several services over one client or over several, and a second user needs a second
identity, not a second connection pool.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from eazy_sdk.auth.core import Auth, AuthProviders
from eazy_sdk.core.errors import PlanError
from eazy_sdk.dependencies import DependencyRegistry
from eazy_sdk.request.signatures import SigningKey, SigningKeyRequirement

type KeyProvider = Callable[[SigningKeyRequirement], SigningKey]
type Observer = Callable[[str, object | None], None]


@dataclass(frozen=True, slots=True)
class Identity:
    """One session scope: configured auth bindings, signing keys, dependencies, observer.

    Pass it to an SDK root (``ShopSdk(client, identity=…)``), where it serves every router
    of that root, or to a single router bound directly to a client.
    """

    auth: tuple[Auth, ...] = ()
    key_provider: KeyProvider | None = None
    dependencies: DependencyRegistry | None = None
    observer: Observer | None = None

    def __post_init__(self) -> None:
        if isinstance(self.auth, Auth) or not isinstance(self.auth, tuple):
            raise TypeError("Identity.auth is a tuple of configured Auth bindings")
        if any(not isinstance(item, Auth) for item in self.auth):
            raise TypeError("Identity.auth accepts only configured Auth bindings")
        if self.dependencies is not None and not isinstance(
            self.dependencies, DependencyRegistry
        ):
            raise TypeError("Identity.dependencies must be a DependencyRegistry")


@dataclass(frozen=True, slots=True)
class _IdentityScope:
    """Runtime view of one identity, resolved once per bound router."""

    auth: AuthProviders = field(default_factory=AuthProviders)
    key_provider: KeyProvider | None = None
    dependencies: DependencyRegistry = field(default_factory=DependencyRegistry)
    observer: Observer | None = None


def _identity_scope(identity: Identity | None) -> _IdentityScope:
    """Merge the configured bindings of one identity into a runtime view."""

    if identity is None:
        return _IdentityScope()
    providers = AuthProviders()
    for binding in identity.auth:
        for scheme, provider in binding._runtime_providers()._entries():
            if providers.get(scheme) is not None:
                raise PlanError(
                    f"identity registers {scheme.diagnostic_name!r} twice"
                )
            providers.register(scheme, provider)
    return _IdentityScope(
        providers,
        identity.key_provider,
        identity.dependencies or DependencyRegistry(),
        identity.observer,
    )


def bind_session_lifecycle(
    scope: _IdentityScope,
    client: object,
    factory: Callable[[object], object],
) -> None:
    """Register the scoped SDK an auth service calls to acquire or refresh a session.

    The factory receives a client scoped to one lifecycle graph, so a login performed for
    a request cannot re-enter itself. A client that is already such a scoped view declines
    the registration, which ends the recursion.
    """

    if not scope.auth:
        return
    if not getattr(client, "_can_bind_sdk", False):
        return
    scoped = getattr(client, "_scoped", None)
    if scoped is None:
        return
    scope.auth.bind_sdk_factory(lambda graph: factory(scoped(graph)))


__all__ = ["Identity"]
