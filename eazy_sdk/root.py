"""SDK roots: composition of routers over one or more clients.

A root owns no operations. It names the routers a consumer reaches (``sdk.books``),
decides which client carries each of them and where each service lives. The service
address itself is a router declaration (``base_url`` on the router class or on a service
mixin in its MRO); ``bind()`` overrides it, or hands one router a different client, at
assembly time.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from eazy_sdk.api import (
    AsyncApi,
    SyncApi,
    _ApiGroup,
    _AsyncClient,
    _OperationDescriptorBase,
    _service_defaults_of,
    _ServiceDefaults,
    _SyncClient,
    validate_base_url,
)
from eazy_sdk.handlers import HandlerProfile
from eazy_sdk.identity import (
    Identity,
    _identity_scope,
    _IdentityScope,
    bind_session_lifecycle,
)
from eazy_sdk.serialization import Serialization

if TYPE_CHECKING:
    from zapros import AsyncBaseHandler, BaseHandler

    from eazy_sdk.clients import ClientConfig


@dataclass(frozen=True, slots=True)
class Binding:
    """One assembly-time decision: which client and which address a router gets."""

    target: type[object]
    client: object | None = None
    base_url: str | None = None


def bind(
    target: type[object],
    *,
    client: object | None = None,
    base_url: str | None = None,
) -> Binding:
    """Bind a router class, or any base class of it, to a client and/or an address.

    Matching runs over the router's MRO and the most specific class wins, so
    ``bind(PaymentsService, base_url=STAGE)`` moves every router of that service at once
    while ``bind(CardApi, client=...)`` moves just one.
    """

    if not isinstance(target, type):
        raise TypeError("bind() requires a router class or one of its base classes")
    if client is None and base_url is None:
        raise TypeError("bind() requires client= or base_url=")
    if base_url is not None:
        validate_base_url(base_url, f"bind({target.__name__}).base_url")
    return Binding(target, client, base_url)


@dataclass(frozen=True, slots=True)
class _GroupPlan:
    client: object
    defaults: _ServiceDefaults


def _reject_root_operations(cls: type[object]) -> None:
    for name in vars(cls):
        if isinstance(inspect.getattr_static(cls, name), _OperationDescriptorBase):
            raise TypeError(
                f"SDK root {cls.__name__} declares operation {name!r}; "
                "a root only composes routers"
            )


class _RootBase:
    """Composition shared by :class:`SyncRoot` and :class:`AsyncRoot`."""

    _api_kind: ClassVar[type[SyncApi | AsyncApi]] = SyncApi
    _root_groups: ClassVar[dict[str, _ApiGroup[Any]]] = {}
    _root_defaults: ClassVar[_ServiceDefaults] = _ServiceDefaults()

    identity: ClassVar[Identity | None] = None
    """Session scope shared by every router of this root; the constructor overrides it."""

    serialization: ClassVar[Serialization | None] = None
    """Model library and encoding backend of this SDK; the constructor overrides it."""

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _reject_root_operations(cls)
        groups: dict[str, _ApiGroup[Any]] = {}
        for base in reversed(cls.__mro__):
            for name, value in vars(base).items():
                if isinstance(value, _ApiGroup):
                    groups[name] = value
        kind = cls._api_kind
        for name, group in groups.items():
            if not issubclass(group.api_type, kind):
                wanted = "async" if kind is AsyncApi else "sync"
                raise TypeError(f"{wanted} API group {name!r} uses the wrong API kind")
        cls._root_groups = groups
        cls._root_defaults = _service_defaults_of(cls)

    def __init__(
        self,
        client: object,
        *,
        bindings: tuple[Binding, ...] = (),
        identity: Identity | None = None,
        serialization: Serialization | None = None,
    ) -> None:
        self._setup(client, tuple(bindings), identity, serialization, scope=None)

    def _setup(
        self,
        client: object,
        bindings: tuple[Binding, ...],
        identity: Identity | None,
        serialization: Serialization | None,
        *,
        scope: _IdentityScope | None,
    ) -> None:
        selected = type(self).identity if identity is None else identity
        if selected is not None and not isinstance(selected, Identity):
            raise TypeError("identity= accepts an Identity")
        chosen = type(self).serialization if serialization is None else serialization
        if chosen is not None and not isinstance(chosen, Serialization):
            raise TypeError("serialization= accepts a Serialization")
        self._default_client = client
        self._bindings = bindings
        self._identity = selected
        self._serialization = chosen if chosen is not None else Serialization()
        self._scope = scope if scope is not None else _identity_scope(selected)
        self._owned: tuple[Any, ...] = ()
        self._closed = False
        self._instances: dict[str, SyncApi | AsyncApi] = {}
        self._plan = _resolve_plan(type(self), client, bindings)
        if scope is None:
            self._register_lifecycle()

    @classmethod
    def _rebuild(
        cls,
        client: object,
        bindings: tuple[Binding, ...],
        serialization: Serialization,
        scope: _IdentityScope,
    ) -> Self:
        """Build a scoped copy for the auth lifecycle without re-registering factories."""

        root = cls.__new__(cls)
        _RootBase._setup(root, client, bindings, None, serialization, scope=scope)
        return root

    def _register_lifecycle(self) -> None:
        bind_session_lifecycle(
            self._scope,
            self._default_client,
            _scoped_factory(type(self), self._bindings, self._serialization, self._scope),
        )

    def _build_group(self, group: _ApiGroup[Any]) -> SyncApi | AsyncApi:
        cached = self._instances.get(group.name)
        if cached is None:
            plan = self._plan[group.name]
            cached = group.api_type(
                cast(Any, plan.client),
                serialization=self._serialization,
                defaults=plan.defaults,
                scope=self._scope,
            )
            self._instances[group.name] = cached
        return cached


class SyncRoot(_RootBase):
    """Synchronous SDK root: routers, their clients and their addresses.

    ``ShopSdk(client)`` runs every router on one client; ``bindings=(bind(...),)`` gives a
    service its own client or address. ``close()`` closes only clients the root created
    itself through :meth:`from_handler`.
    """

    _api_kind: ClassVar[type[SyncApi | AsyncApi]] = SyncApi

    def __init__(
        self,
        client: _SyncClient,
        *,
        bindings: tuple[Binding, ...] = (),
        identity: Identity | None = None,
        serialization: Serialization | None = None,
    ) -> None:
        super().__init__(
            client, bindings=bindings, identity=identity, serialization=serialization
        )

    @classmethod
    def from_handler(
        cls,
        *,
        handler: BaseHandler,
        base_url: str = "",
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
        bindings: tuple[Binding, ...] = (),
        identity: Identity | None = None,
        serialization: Serialization | None = None,
    ) -> Self:
        """Build the default client over ``handler`` and let the root own it."""

        from eazy_sdk.clients import Client

        client = Client(
            base_url=base_url,
            handler=handler,
            config=config,
            owns_handler=owns_handler,
            profile=profile,
        )
        root = cls(
            client, bindings=bindings, identity=identity, serialization=serialization
        )
        root._owned = (client,)
        return root

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for client in self._owned:
            client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class AsyncRoot(_RootBase):
    """Asynchronous SDK root: routers, their clients and their addresses.

    ``aclose()`` closes only clients the root created itself through
    :meth:`from_handler`.
    """

    _api_kind: ClassVar[type[SyncApi | AsyncApi]] = AsyncApi

    def __init__(
        self,
        client: _AsyncClient,
        *,
        bindings: tuple[Binding, ...] = (),
        identity: Identity | None = None,
        serialization: Serialization | None = None,
    ) -> None:
        super().__init__(
            client, bindings=bindings, identity=identity, serialization=serialization
        )

    @classmethod
    def from_handler(
        cls,
        *,
        handler: AsyncBaseHandler,
        base_url: str = "",
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
        bindings: tuple[Binding, ...] = (),
        identity: Identity | None = None,
        serialization: Serialization | None = None,
    ) -> Self:
        """Build the default client over ``handler`` and let the root own it."""

        from eazy_sdk.clients import AsyncClient

        client = AsyncClient(
            base_url=base_url,
            handler=handler,
            config=config,
            owns_handler=owns_handler,
            profile=profile,
        )
        root = cls(
            client, bindings=bindings, identity=identity, serialization=serialization
        )
        root._owned = (client,)
        return root

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for client in self._owned:
            await client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


def _scoped_factory(
    root_type: type[_RootBase],
    bindings: tuple[Binding, ...],
    serialization: Serialization,
    scope: _IdentityScope,
) -> Any:
    def build(scoped: object) -> object:
        return root_type._rebuild(scoped, bindings, serialization, scope)

    return build


def _resolve_plan(
    root_type: type[_RootBase],
    default_client: object,
    bindings: tuple[Binding, ...],
) -> dict[str, _GroupPlan]:
    seen: list[type[object]] = []
    for binding in bindings:
        if not isinstance(binding, Binding):
            raise TypeError("bindings accepts only bind() results")
        if any(binding.target is target for target in seen):
            raise TypeError(f"bind({binding.target.__name__}) is declared twice")
        seen.append(binding.target)
    used: set[type[object]] = set()
    plan: dict[str, _GroupPlan] = {}
    for name, group in root_type._root_groups.items():
        router = group.api_type
        candidates = [
            binding for binding in bindings if binding.target in router.__mro__
        ]
        candidates.sort(key=lambda binding: router.__mro__.index(binding.target))
        used.update(binding.target for binding in candidates)
        client = _most_specific(router, candidates, "client") or default_client
        address = _most_specific(router, candidates, "base_url")
        defaults = root_type._root_defaults.extend(router._service_defaults)
        if address is not None:
            defaults = replace(defaults, base_url=cast(str, address))
        _validate_router(name, router, defaults)
        plan[name] = _GroupPlan(client, defaults)
    unused = [binding.target.__name__ for binding in bindings if binding.target not in used]
    if unused:
        raise TypeError(
            f"bind() targets no router of {root_type.__name__}: {', '.join(sorted(unused))}"
        )
    return plan


def _most_specific(
    router: type[object],
    candidates: list[Binding],
    field: str,
) -> object | None:
    declaring = [binding for binding in candidates if getattr(binding, field) is not None]
    if not declaring:
        return None
    winner = declaring[0]
    for other in declaring[1:]:
        if not issubclass(winner.target, other.target):
            raise TypeError(
                f"{router.__name__} matches conflicting bind({field}=) declarations on "
                f"{winner.target.__name__} and {other.target.__name__}"
            )
    return cast("object | None", getattr(winner, field))


def _validate_router(name: str, router: type[object], defaults: _ServiceDefaults) -> None:
    """Resolve every operation once, so an allowlist violation fails at assembly."""

    for attribute in dir(router):
        descriptor = inspect.getattr_static(router, attribute)
        if isinstance(descriptor, _OperationDescriptorBase):
            try:
                descriptor.resolve(defaults)
            except TypeError as error:
                raise TypeError(f"api group {name!r}: {error}") from error


__all__ = ["AsyncRoot", "Binding", "SyncRoot", "bind"]
