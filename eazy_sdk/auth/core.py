"""Identity-based security schemes, alternatives and auth execution records."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, cast

from eazy_sdk.auth.session import LifecycleGraph, SessionKey, SessionRevision
from eazy_sdk.core.errors import PlanError
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.core.kernel import (
    Set,
    ValuePatch,
    ValueSlot,
    ValueValidator,
)

if TYPE_CHECKING:
    from eazy_sdk.compile.http_compiler import CompiledContract


class AuthLocation(Enum):
    HEADER = "header"
    QUERY = "query"
    COOKIE = "cookie"


class SessionSelector[TSession, TValue](Protocol):
    def select(self, session: TSession) -> TValue: ...


@dataclass(frozen=True, slots=True)
class IdentitySessionSelector[T]:
    def select(self, session: T) -> T:
        return session


@dataclass(frozen=True, slots=True)
class _NonEmptyCredentialValidator:
    def __call__(self, value: object) -> str:
        if not isinstance(value, str) or not value:
            raise TypeError("credential must be a non-empty string")
        return value


@dataclass(frozen=True, slots=True)
class _BasicCredentialValidator:
    def __call__(self, value: object) -> tuple[str, str]:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not all(isinstance(item, str) for item in value)
        ):
            raise TypeError("basic credentials must contain exactly two strings")
        return value


@dataclass(frozen=True, slots=True)
class AttributeSessionSelector[TSession, TValue]:
    name: str

    def select(self, session: TSession) -> TValue:
        try:
            return cast(TValue, getattr(session, self.name))
        except AttributeError as exc:
            raise PlanError(f"session has no field {self.name!r}") from exc


@dataclass(frozen=True, slots=True)
class AuthPlacement[TSession, TValue]:
    location: AuthLocation
    name: str
    select: SessionSelector[TSession, TValue]
    prefix: str = ""
    secret: bool = True

    def value(self, session: TSession) -> str:
        selected = self.select.select(session)
        reveal = getattr(selected, "get_secret_value", None)
        if callable(reveal):
            selected = reveal()
        return self.prefix + str(selected)


@dataclass(frozen=True, slots=True, eq=False)
class AuthScheme[TSession]:
    diagnostic_name: str
    session_type: ValueValidator[TSession]
    placements: tuple[AuthPlacement[TSession, Any], ...]
    scope: RequestScope = field(default_factory=RequestScope)

    def static(self, value: TSession) -> Auth:
        """Bind one ready credential without exposing provider registry plumbing."""

        providers = AuthProviders()
        providers.register(
            self,
            StaticAuthProvider(
                self,
                value,
                AuthProviderIdentity(f"static:{self.diagnostic_name}"),
            ),
        )
        return Auth._bind(self, providers)


@dataclass(frozen=True, slots=True)
class SecurityAlternative:
    schemes: tuple[AuthScheme[Any], ...]


@dataclass(frozen=True, slots=True)
class SecurityPolicy:
    alternatives: tuple[SecurityAlternative, ...]
    optional: bool = False


@dataclass(frozen=True, slots=True, eq=False)
class AuthProviderIdentity:
    name: str


@dataclass(frozen=True, slots=True)
class AuthExecution[TSession]:
    scheme: AuthScheme[TSession]
    provider: AuthProviderIdentity
    session_key: SessionKey
    revision: SessionRevision
    alternative: int


@dataclass(frozen=True, slots=True)
class ResolvedAuth[TSession]:
    value: TSession = field(repr=False)
    execution: AuthExecution[TSession]


class AuthProvider[TSession](Protocol):
    async def resolve(self) -> ResolvedAuth[TSession]: ...


@dataclass(frozen=True, slots=True)
class StaticAuthProvider[TSession]:
    scheme: AuthScheme[TSession]
    value: TSession = field(repr=False)
    identity: AuthProviderIdentity
    key: SessionKey = field(default_factory=lambda: SessionKey("static"))

    async def resolve(self) -> ResolvedAuth[TSession]:
        validated = self.scheme.session_type(self.value)
        return ResolvedAuth(
            validated,
            AuthExecution(self.scheme, self.identity, self.key, SessionRevision(0), -1),
        )


class AuthProviders:
    def __init__(self) -> None:
        self._providers: dict[int, AuthProvider[Any]] = {}

    def register[T](self, scheme: AuthScheme[T], provider: AuthProvider[T]) -> None:
        if id(scheme) in self._providers:
            raise PlanError(f"auth scheme already registered: {scheme.diagnostic_name}")
        self._providers[id(scheme)] = provider

    def get[T](self, scheme: AuthScheme[T]) -> AuthProvider[T] | None:
        return cast(AuthProvider[T] | None, self._providers.get(id(scheme)))

    def __bool__(self) -> bool:
        return bool(self._providers)

    def bind_sdk_factory(self, factory: Callable[[LifecycleGraph], object]) -> None:
        for provider in self._providers.values():
            bind = getattr(provider, "bind_sdk_factory", None)
            if callable(bind):
                bind(factory)

    async def adopt[T](self, scheme: AuthScheme[T], value: T) -> object:
        provider = self.get(scheme)
        if provider is None:
            raise PlanError(f"auth provider is not configured: {scheme.diagnostic_name}")
        adopt = getattr(provider, "adopt", None)
        if not callable(adopt):
            raise PlanError(f"auth provider cannot adopt sessions: {scheme.diagnostic_name}")
        return await adopt(value)


@dataclass(frozen=True, slots=True, init=False)
class Auth:
    """One configured auth scheme and its private provider binding."""

    scheme: AuthScheme[Any]
    _providers: AuthProviders = field(repr=False)

    def __init__(self) -> None:
        raise TypeError("use scheme.static(), session_auth() or session_cookie()")

    @classmethod
    def _bind(cls, scheme: AuthScheme[Any], providers: AuthProviders) -> Auth:
        binding = object.__new__(cls)
        object.__setattr__(binding, "scheme", scheme)
        object.__setattr__(binding, "_providers", providers)
        return binding

    def _runtime_providers(self) -> AuthProviders:
        return self._providers

    async def adopt(self, value: object) -> object:
        """Adopt a session produced by a completed account lifecycle transition."""

        return await self._providers.adopt(self.scheme, value)


def _has_refreshable_security(
    executions: tuple[AuthExecution[Any], ...], providers: AuthProviders
) -> bool:
    return any(_refresh_callback(execution, providers) is not None for execution in executions)


async def _refresh_security(
    executions: tuple[AuthExecution[Any], ...],
    providers: AuthProviders,
    graph: LifecycleGraph | None = None,
) -> None:
    callbacks = [
        callback
        for execution in executions
        if (callback := _refresh_callback(execution, providers, graph)) is not None
    ]
    if not callbacks:
        raise PlanError("selected security alternative has no refreshable session")
    if len(callbacks) > 1:
        raise PlanError("cannot unambiguously refresh multiple selected auth sessions")
    await callbacks[0]()


def _refresh_callback(
    execution: AuthExecution[Any],
    providers: AuthProviders,
    graph: LifecycleGraph | None = None,
) -> Callable[[], Awaitable[ResolvedAuth[Any]]] | None:
    provider = providers.get(execution.scheme)
    if provider is None or not getattr(provider, "can_refresh", False):
        return None
    refresh = getattr(provider, "refresh_execution", None)
    if not callable(refresh):
        return None

    async def invoke() -> ResolvedAuth[Any]:
        return cast(ResolvedAuth[Any], await refresh(execution, graph))

    return invoke


async def resolve_security(
    policy: SecurityPolicy | None,
    providers: AuthProviders,
    compiled: CompiledContract[object],
    *,
    graph: LifecycleGraph | None = None,
) -> tuple[tuple[AuthExecution[Any], ...], ValuePatch]:
    if policy is None:
        return (), ValuePatch(())
    for alternative_index, alternative in enumerate(policy.alternatives):
        selected = [(scheme, providers.get(scheme)) for scheme in alternative.schemes]
        if any(provider is None for _, provider in selected):
            continue
        operations: list[Set[Any]] = []
        executions: list[AuthExecution[Any]] = []
        for scheme, provider in selected:
            assert provider is not None
            resolve_scoped = getattr(provider, "resolve_scoped", None)
            resolved = (
                await resolve_scoped(graph, compiled.contract.operation_id)
                if graph is not None and callable(resolve_scoped)
                else await provider.resolve()
            )
            execution = dataclass_replace_alternative(resolved.execution, alternative_index)
            executions.append(execution)
            operations.extend(_binding_operations(compiled, scheme, resolved.value))
        return tuple(executions), ValuePatch(tuple(operations))
    if policy.optional:
        return (), ValuePatch(())
    raise PlanError("no complete security alternative is available")


def _binding_operations(
    compiled: CompiledContract[object], scheme: AuthScheme[Any], value: object
) -> list[Set[Any]]:
    output: list[Set[Any]] = []
    for placement in scheme.placements:
        slots: Any
        if placement.location is AuthLocation.HEADER:
            slots = compiled.header_slots
        elif placement.location is AuthLocation.QUERY:
            slots = compiled.query_slots
        else:
            slots = compiled.cookie_slots
        slot: ValueSlot[Any] | None = slots.get(placement.name)
        if slot is None:
            raise PlanError(
                f"auth target is not declared: {placement.location.value}.{placement.name}"
            )
        output.append(Set(slot, placement.value(value)))
    return output


def dataclass_replace_alternative[T](
    execution: AuthExecution[T], alternative: int
) -> AuthExecution[T]:
    return AuthExecution(
        execution.scheme,
        execution.provider,
        execution.session_key,
        execution.revision,
        alternative,
    )


def BearerScheme(name: str = "bearer") -> AuthScheme[str]:
    return AuthScheme(
        name,
        _NonEmptyCredentialValidator(),
        (
            AuthPlacement(
                AuthLocation.HEADER,
                "Authorization",
                IdentitySessionSelector(),
                "Bearer ",
            ),
        ),
    )


class ApiKeyScheme:
    @staticmethod
    def header(header_name: str, *, name: str = "api_key") -> AuthScheme[str]:
        return AuthScheme(
            name,
            _NonEmptyCredentialValidator(),
            (AuthPlacement(AuthLocation.HEADER, header_name, IdentitySessionSelector()),),
        )

    @staticmethod
    def query(parameter_name: str, *, name: str = "api_key") -> AuthScheme[str]:
        return AuthScheme(
            name,
            _NonEmptyCredentialValidator(),
            (AuthPlacement(AuthLocation.QUERY, parameter_name, IdentitySessionSelector()),),
        )


def BasicScheme(name: str = "basic") -> AuthScheme[tuple[str, str]]:
    return AuthScheme(
        name,
        _BasicCredentialValidator(),
        (
            AuthPlacement(
                AuthLocation.HEADER,
                "Authorization",
                BasicSelector(),
                "Basic ",
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class BasicSelector:
    def select(self, session: tuple[str, str]) -> str:
        return base64.b64encode(f"{session[0]}:{session[1]}".encode()).decode()


def CookieScheme(cookie_name: str, *, name: str = "cookie") -> AuthScheme[str]:
    return AuthScheme(
        name,
        _NonEmptyCredentialValidator(),
        (AuthPlacement(AuthLocation.COOKIE, cookie_name, IdentitySessionSelector()),),
    )


def all_of(*schemes: AuthScheme[Any]) -> SecurityAlternative:
    return SecurityAlternative(schemes)


def any_of(*alternatives: AuthScheme[Any] | SecurityAlternative) -> SecurityPolicy:
    return SecurityPolicy(
        tuple(
            item if isinstance(item, SecurityAlternative) else SecurityAlternative((item,))
            for item in alternatives
        )
    )
