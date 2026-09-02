"""Generic typed session lifecycle and explicit scoped resolution graph."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from eazy_sdk._internal.errors import GraphError, PlanError
from eazy_sdk._internal.kernel import ValueValidator
from eazy_sdk.accounts.session import (
    ExpiresAt,
    MemorySessionStore,
    RefreshToken,
    SessionAcquirer,
    SessionCredentialsRequiredError,
    SessionKey,
    SessionLifecycle,
    SessionLifecycleConfig,
    SessionLifecycleCycleError,
    SessionLifecycleError,
    SessionRecord,
    SessionRefresher,
    SessionRevisionError,
    SessionStore,
    SessionValidationError,
    SessionValidator,
    StoredSession,
)
from eazy_sdk.accounts.session import (
    LifecycleGraph as ResolutionGraph,
)
from eazy_sdk.models import default_model_adapters
from eazy_sdk.response import ResponseEnvelope

from .cookies import HttpCookieSession, parse_session_cookie
from .core import (
    AttributeSessionSelector,
    Auth,
    AuthExecution,
    AuthLocation,
    AuthPlacement,
    AuthProviderIdentity,
    AuthProviders,
    AuthScheme,
    ResolvedAuth,
)


@dataclass(frozen=True, slots=True)
class Bearer:
    """Mark the session field placed as an HTTP Bearer credential."""

    header: str = "Authorization"
    prefix: str = "Bearer "


class SessionCodec[T](Protocol):
    def encode(self, value: T) -> object: ...

    def decode(self, value: object) -> T: ...


class AuthContext[TSdk = Any](Protocol):
    @property
    def sdk(self) -> TSdk: ...

    def capture[T](self, envelope: ResponseEnvelope[T, Any]) -> T: ...


class AuthService[TCredentials, TSession](Protocol):
    async def acquire(
        self,
        credentials: TCredentials,
        context: AuthContext,
    ) -> TSession: ...


class ResolutionCycleError(GraphError):
    pass


@dataclass(frozen=True, slots=True)
class AuthFlowContext[TSdk]:
    sdk: TSdk
    session_key: SessionKey
    deadline: datetime | None
    resolution_graph: ResolutionGraph
    _responses: list[object] = field(default_factory=list, repr=False, compare=False)

    def capture[T](self, envelope: ResponseEnvelope[T, Any]) -> T:
        self._responses.append(envelope.response)
        return envelope.value

    @property
    def responses(self) -> tuple[object, ...]:
        return tuple(self._responses)


@dataclass(frozen=True, slots=True)
class SessionAuth[TCredentials, TSession, TSdk]:
    scheme: AuthScheme[TSession]
    key: SessionKey
    sdk_factory: Callable[[ResolutionGraph], TSdk]
    store: SessionStore[TSession]
    validate: SessionValidator[TSession]
    credentials: TCredentials | None = field(default=None, repr=False)
    initial_session: TSession | None = field(default=None, repr=False)
    acquire: SessionAcquirer[TCredentials, TSession, AuthFlowContext[TSdk]] | None = None
    refresh: SessionRefresher[TSession, AuthFlowContext[TSdk]] | None = None
    identity: AuthProviderIdentity = field(default_factory=lambda: AuthProviderIdentity("session"))

    def __post_init__(self) -> None:
        if self.credentials is not None and self.initial_session is not None:
            raise ValueError("credentials and session are mutually exclusive")


class SessionProvider[TCredentials, TSession, TSdk]:
    def __init__(self, config: SessionAuth[TCredentials, TSession, TSdk]) -> None:
        self.config = config
        self._sdk_factory: Callable[[ResolutionGraph], TSdk] = config.sdk_factory
        self.lifecycle = SessionLifecycle(
            SessionLifecycleConfig(
                key=config.key,
                context_factory=self._context,
                store=config.store,
                validate=config.validate,
                parse=config.scheme.session_type,
                credentials=config.credentials,
                initial_session=config.initial_session,
                acquire=config.acquire,
                refresh=config.refresh,
                diagnostic_name=config.scheme.diagnostic_name,
            )
        )

    def bind_sdk_factory(self, factory: Callable[[ResolutionGraph], object]) -> None:
        self._sdk_factory = cast(Callable[[ResolutionGraph], TSdk], factory)

    @property
    def can_refresh(self) -> bool:
        return self.lifecycle.can_refresh

    async def resolve(
        self, graph: ResolutionGraph | None = None, operation_id: str = "operation"
    ) -> ResolvedAuth[TSession]:
        return self._resolved_from_record(
            await self._lifecycle_call(self.lifecycle.resolve(graph, operation_id))
        )

    async def resolve_scoped(
        self,
        graph: ResolutionGraph,
        operation_id: str,
    ) -> ResolvedAuth[TSession]:
        return await self.resolve(graph, operation_id)

    async def refresh(
        self,
        current: StoredSession[TSession],
        graph: ResolutionGraph | None = None,
    ) -> ResolvedAuth[TSession]:
        return self._resolved_from_record(
            await self._lifecycle_call(self.lifecycle.refresh(current, graph))
        )

    async def adopt(self, value: TSession) -> ResolvedAuth[TSession]:
        return self._resolved_from_record(await self._lifecycle_call(self.lifecycle.adopt(value)))

    async def refresh_execution(
        self,
        execution: AuthExecution[TSession],
        graph: ResolutionGraph | None = None,
    ) -> ResolvedAuth[TSession]:
        if (
            execution.scheme is not self.config.scheme
            or execution.provider is not self.config.identity
        ):
            raise PlanError("auth execution does not belong to this session provider")
        return self._resolved_from_record(
            await self._lifecycle_call(self.lifecycle.refresh_revision(execution.revision, graph))
        )

    def _context(self, graph: ResolutionGraph) -> AuthFlowContext[TSdk]:
        return AuthFlowContext(
            self._sdk_factory(graph),
            self.config.key,
            None,
            graph,
        )

    async def _lifecycle_call(
        self,
        result: Awaitable[SessionRecord[TSession]],
    ) -> SessionRecord[TSession]:
        try:
            return await result
        except SessionCredentialsRequiredError as exc:
            raise AuthCredentialsRequiredError(str(exc)) from exc
        except SessionLifecycleCycleError as exc:
            raise ResolutionCycleError(
                str(exc).replace("lifecycle cycle", "resolution cycle")
            ) from exc
        except (SessionValidationError, SessionRevisionError, SessionLifecycleError) as exc:
            raise PlanError(str(exc)) from exc

    def _resolved_from_record(self, record: SessionRecord[TSession]) -> ResolvedAuth[TSession]:
        return ResolvedAuth(
            record.value,
            AuthExecution(
                self.config.scheme,
                self.config.identity,
                record.key,
                record.revision,
                -1,
            ),
        )


class AuthCredentialsRequiredError(PlanError):
    pass


@dataclass(frozen=True, slots=True)
class _SessionModel:
    model: type[object]
    bearer_field: str
    bearer: Bearer
    refresh_field: str | None
    expires_field: str | None
    expires: ExpiresAt | None
    clock: Callable[[], datetime]

    @classmethod
    def compile(
        cls,
        model: type[object],
        clock: Callable[[], datetime],
    ) -> _SessionModel:
        fields = default_model_adapters().fields(model)

        bearer: list[tuple[str, Bearer]] = []
        refresh: list[str] = []
        expires: list[tuple[str, ExpiresAt]] = []
        for model_field in fields:
            for marker in model_field.metadata:
                if isinstance(marker, Bearer):
                    bearer.append((model_field.name, marker))
                elif isinstance(marker, RefreshToken):
                    refresh.append(model_field.name)
                elif isinstance(marker, ExpiresAt):
                    expires.append((model_field.name, marker))
        if len(bearer) != 1:
            raise SessionConfigurationError("session model must declare exactly one Bearer field")
        if len(refresh) > 1:
            raise SessionConfigurationError(
                "session model cannot declare multiple RefreshToken fields"
            )
        if len(expires) > 1:
            raise SessionConfigurationError(
                "session model cannot declare multiple ExpiresAt fields"
            )
        return cls(
            model,
            bearer[0][0],
            bearer[0][1],
            refresh[0] if refresh else None,
            expires[0][0] if expires else None,
            expires[0][1] if expires else None,
            clock,
        )

    @classmethod
    def from_fields(
        cls,
        model: type[object],
        *,
        bearer_field: str,
        refresh_field: str | None,
        expires_field: str | None,
        expires_leeway: timedelta,
        clock: Callable[[], datetime],
    ) -> _SessionModel:
        fields = default_model_adapters().fields(model)
        field_names = {field.name for field in fields}
        selected = {
            "Bearer": bearer_field,
            "RefreshToken": refresh_field,
            "ExpiresAt": expires_field,
        }
        for role, name in selected.items():
            if name is not None and name not in field_names:
                raise SessionConfigurationError(
                    f"generated {role} field {name!r} is absent from the session model"
                )
        return cls(
            model,
            bearer_field,
            Bearer(),
            refresh_field,
            expires_field,
            ExpiresAt(expires_leeway) if expires_field is not None else None,
            clock,
        )

    def parse(self, value: object) -> object:
        return default_model_adapters().load(self.model, value)

    def is_valid(self, value: object) -> bool:
        if self.expires_field is None or self.expires is None:
            return True
        expires_at = getattr(value, self.expires_field, None)
        if not isinstance(expires_at, datetime):
            raise TypeError("ExpiresAt field must contain a datetime")
        now = self.clock()
        if expires_at.tzinfo is None or now.tzinfo is None:
            raise TypeError("ExpiresAt and clock values must be timezone-aware")
        return expires_at > now + self.expires.leeway

    def scheme[T](self, diagnostic_name: str) -> AuthScheme[T]:
        return AuthScheme(
            diagnostic_name,
            cast(ValueValidator[T], self.parse),
            (
                AuthPlacement(
                    AuthLocation.HEADER,
                    self.bearer.header,
                    AttributeSessionSelector(self.bearer_field),
                    self.bearer.prefix,
                ),
            ),
        )


class SessionConfigurationError(PlanError):
    pass


def session_auth[TCredentials, TSession](
    session_model: type[TSession],
    *,
    credentials: TCredentials | None = None,
    session: TSession | None = None,
    service: AuthService[TCredentials, TSession],
    store: SessionStore[TSession] | None = None,
    identity: str | None = None,
    name: str = "session",
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Auth:
    """Configure a model-backed session without public runtime plumbing."""

    schema = _SessionModel.compile(cast(type[object], session_model), clock)
    return _build_session_auth(
        schema,
        scheme=None,
        credentials=credentials,
        session=session,
        service=service,
        store=store,
        identity=identity,
        name=name,
    )


@dataclass(frozen=True, slots=True, eq=False)
class SessionScheme[TSession](AuthScheme[TSession]):
    """Reusable API security scheme with its model-backed lifecycle factory."""

    _schema: _SessionModel | None = field(default=None, repr=False, compare=False)

    def configure[TCredentials](
        self,
        *,
        credentials: TCredentials | None = None,
        session: TSession | None = None,
        service: AuthService[TCredentials, TSession],
        store: SessionStore[TSession] | None = None,
        identity: str | None = None,
    ) -> Auth:
        schema = self._schema
        if schema is None:
            raise SessionConfigurationError("session scheme has no model schema")
        return _build_session_auth(
            schema,
            scheme=self,
            credentials=credentials,
            session=session,
            service=service,
            store=store,
            identity=identity,
            name=self.diagnostic_name,
        )


def session_scheme[TSession](
    session_model: type[TSession],
    *,
    name: str = "session",
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SessionScheme[TSession]:
    """Declare reusable API security and model-backed session configuration."""

    schema = _SessionModel.compile(cast(type[object], session_model), clock)
    base: AuthScheme[TSession] = schema.scheme(name)
    return SessionScheme(
        base.diagnostic_name,
        base.session_type,
        base.placements,
        base.scope,
        schema,
    )


def _generated_session_auth[TCredentials, TSession](
    session_model: type[TSession],
    *,
    bearer_field: str,
    refresh_field: str | None,
    expires_field: str | None,
    expires_leeway_seconds: float,
    credentials: TCredentials | None,
    session: TSession | None,
    service: AuthService[TCredentials, TSession],
    scheme: AuthScheme[TSession],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Auth:
    """Generated-source lowering; consumer code uses its SDK factory."""

    schema = _SessionModel.from_fields(
        cast(type[object], session_model),
        bearer_field=bearer_field,
        refresh_field=refresh_field,
        expires_field=expires_field,
        expires_leeway=timedelta(seconds=expires_leeway_seconds),
        clock=clock,
    )
    return _build_session_auth(
        schema,
        scheme=scheme,
        credentials=credentials,
        session=session,
        service=service,
        store=None,
        identity=None,
        name="generated-session",
    )


def _generated_session_scheme[TSession](
    session_model: type[TSession],
    *,
    name: str,
    bearer_field: str,
    refresh_field: str | None,
    expires_field: str | None,
    expires_leeway_seconds: float,
) -> AuthScheme[TSession]:
    schema = _SessionModel.from_fields(
        cast(type[object], session_model),
        bearer_field=bearer_field,
        refresh_field=refresh_field,
        expires_field=expires_field,
        expires_leeway=timedelta(seconds=expires_leeway_seconds),
        clock=lambda: datetime.now(UTC),
    )
    return schema.scheme(name)


def _build_session_auth[TCredentials, TSession](
    schema: _SessionModel,
    *,
    scheme: AuthScheme[TSession] | None,
    credentials: TCredentials | None,
    session: TSession | None,
    service: AuthService[TCredentials, TSession],
    store: SessionStore[TSession] | None,
    identity: str | None,
    name: str,
) -> Auth:
    if (credentials is None) == (session is None):
        raise ValueError("provide exactly one of credentials or session")
    has_refresh = _validate_auth_service(service)
    selected_scheme = scheme or schema.scheme(name)
    selected_store = store or MemorySessionStore[TSession]()
    key = SessionKey(f"session:{identity or 'client'}")
    config = SessionAuth(
        scheme=selected_scheme,
        key=key,
        sdk_factory=lambda _graph: None,
        store=selected_store,
        validate=schema.is_valid,
        credentials=credentials,
        initial_session=session,
        acquire=(
            cast(SessionAcquirer[TCredentials, TSession, AuthFlowContext[None]], service)
            if credentials is not None
            else None
        ),
        refresh=(
            cast(SessionRefresher[TSession, AuthFlowContext[None]], service)
            if has_refresh
            else None
        ),
        identity=AuthProviderIdentity(f"session:{identity or name}"),
    )
    providers = AuthProviders()
    providers.register(selected_scheme, SessionProvider(config))
    return Auth._bind(selected_scheme, providers)


_MISSING_SERVICE_METHOD = object()


def _validate_auth_service(service: object) -> bool:
    """Fail while configuring auth instead of during the first protected call."""

    service_name = type(service).__qualname__
    acquire = getattr(service, "acquire", _MISSING_SERVICE_METHOD)
    if acquire is _MISSING_SERVICE_METHOD:
        raise SessionConfigurationError(
            f"auth service {service_name} must define "
            "async acquire(credentials, context)"
        )
    _validate_async_service_method(
        service_name,
        "acquire",
        acquire,
        arguments="credentials, context",
    )

    refresh = getattr(service, "refresh", _MISSING_SERVICE_METHOD)
    if refresh is _MISSING_SERVICE_METHOD:
        return False
    _validate_async_service_method(
        service_name,
        "refresh",
        refresh,
        arguments="session, context",
        optional=True,
    )
    return True


def _validate_async_service_method(
    service_name: str,
    method_name: str,
    method: object,
    *,
    arguments: str,
    optional: bool = False,
) -> None:
    if not callable(method):
        suffix = " or omitted" if optional else ""
        raise SessionConfigurationError(
            f"auth service {service_name}.{method_name} must be callable{suffix}"
        )
    if not inspect.iscoroutinefunction(method):
        raise SessionConfigurationError(
            f"auth service {service_name}.{method_name} must be declared with async def"
        )
    try:
        inspect.signature(method).bind(object(), object())
    except (TypeError, ValueError):
        raise SessionConfigurationError(
            f"auth service {service_name}.{method_name} must accept "
            f"({arguments}) as positional arguments"
        ) from None


@dataclass(slots=True)
class _CookieService[TCredentials, TResult]:
    cookie_name: str
    credentials: TCredentials = field(repr=False)
    service: AuthService[TCredentials, TResult]
    clock: Callable[[], datetime]

    async def acquire(
        self,
        credentials: TCredentials,
        context: AuthFlowContext[None],
    ) -> HttpCookieSession:
        await self.service.acquire(credentials, context)
        return self._parse(context)

    async def refresh(
        self,
        _session: HttpCookieSession,
        context: AuthFlowContext[None],
    ) -> HttpCookieSession:
        await self.service.acquire(self.credentials, context)
        return self._parse(context)

    def _parse(self, context: AuthFlowContext[None]) -> HttpCookieSession:
        try:
            return parse_session_cookie(
                context.responses,
                self.cookie_name,
                now=self.clock(),
            )
        except PlanError as exc:
            raise SessionConfigurationError(str(exc)) from exc


def session_cookie[TCredentials, TResult](
    cookie_name: str,
    *,
    credentials: TCredentials,
    service: AuthService[TCredentials, TResult],
    store: SessionStore[HttpCookieSession] | None = None,
    identity: str | None = None,
    name: str = "session-cookie",
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Auth:
    """Configure a transport-owned cookie session from staged Set-Cookie responses."""

    if not cookie_name:
        raise ValueError("cookie_name cannot be empty")
    scheme: AuthScheme[HttpCookieSession] = AuthScheme(
        name,
        lambda value: (
            value
            if isinstance(value, HttpCookieSession)
            else (_ for _ in ()).throw(TypeError("invalid cookie session"))
        ),
        (
            AuthPlacement(
                AuthLocation.COOKIE,
                cookie_name,
                AttributeSessionSelector("value"),
            ),
        ),
    )
    wrapped = _CookieService(cookie_name, credentials, service, clock)
    config = SessionAuth(
        scheme=scheme,
        key=SessionKey(f"cookie:{identity or 'client'}"),
        sdk_factory=lambda _graph: None,
        store=store or MemorySessionStore[HttpCookieSession](),
        validate=lambda value: value.is_active(clock()),
        credentials=credentials,
        acquire=wrapped,
        refresh=wrapped,
        identity=AuthProviderIdentity(f"cookie:{identity or name}"),
    )
    providers = AuthProviders()
    providers.register(scheme, SessionProvider(config))
    return Auth._bind(scheme, providers)
