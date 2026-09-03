"""Transport-neutral session lifecycle primitives.

This module deliberately knows nothing about HTTP request placement, response status codes,
clients, adapters, or browser runtimes.  Transport bindings supply a context factory and map the
resulting session record into their own execution model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from eazy_sdk.auth.lifecycle import LifecycleCycleError, LifecycleGraph, LifecycleNode
from eazy_sdk.core.errors import ConfigurationError, EazySdkError, PlanError
from eazy_sdk.models import default_model_adapters


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """Mark the session value used by an application-specific refresh service."""


@dataclass(frozen=True, slots=True)
class ExpiresAt:
    """Mark absolute session expiry and its proactive refresh safety window."""

    leeway: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if self.leeway < timedelta(0):
            raise ValueError("expiry leeway cannot be negative")


@dataclass(frozen=True, slots=True)
class SessionKey:
    """Non-secret identity used for persistence and singleflight."""

    value: str

    def __post_init__(self) -> None:
        lowered = self.value.lower()
        if not self.value or any(token in lowered for token in ("bearer ", "password=")):
            raise ValueError("session key must be a non-secret identity")


@dataclass(frozen=True, slots=True)
class SessionRevision:
    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("session revision cannot be negative")


@dataclass(frozen=True, slots=True)
class StoredSession[TSession]:
    value: TSession = field(repr=False)
    revision: SessionRevision


@dataclass(frozen=True, slots=True)
class SessionRecord[TSession]:
    """A validated session together with its persistence identity and revision."""

    value: TSession = field(repr=False)
    key: SessionKey
    revision: SessionRevision


class SessionStore[TSession](Protocol):
    async def load(self, key: SessionKey) -> StoredSession[TSession] | None: ...

    async def save(
        self,
        key: SessionKey,
        value: TSession,
        revision: SessionRevision,
    ) -> None: ...

    async def invalidate(
        self,
        key: SessionKey,
        expected: SessionRevision | None = None,
    ) -> None: ...


class SessionValidator[TSession](Protocol):
    def __call__(self, value: TSession) -> bool: ...


class SessionAcquirer[TCredentials, TSession, TContext](Protocol):
    async def acquire(self, credentials: TCredentials, context: TContext) -> TSession: ...


class SessionRefresher[TSession, TContext](Protocol):
    async def refresh(self, session: TSession, context: TContext) -> TSession: ...


class SessionAdopter[TSession](Protocol):
    async def adopt(self, value: TSession) -> object: ...


class SessionBridge[TSource, TTarget](Protocol):
    """Explicit conversion boundary between otherwise opaque session representations."""

    def convert(self, value: TSource) -> TTarget: ...


@dataclass(frozen=True, slots=True)
class BridgedSessionAdopter[TSource, TTarget]:
    bridge: SessionBridge[TSource, TTarget]
    target: SessionAdopter[TTarget]

    async def adopt(self, value: TSource) -> object:
        return await self.target.adopt(self.bridge.convert(value))


class SessionLifecycleError(EazySdkError, RuntimeError):
    pass


class SessionCredentialsRequiredError(SessionLifecycleError):
    pass


class SessionValidationError(SessionLifecycleError):
    pass


class SessionRevisionError(SessionLifecycleError):
    pass


class SessionLifecycleCycleError(SessionLifecycleError, LifecycleCycleError):
    pass


class SessionConfigurationError(SessionLifecycleError, PlanError, ConfigurationError):
    pass


@dataclass(frozen=True, slots=True)
class SessionLifecycleConfig[TCredentials, TSession, TContext]:
    key: SessionKey
    context_factory: Callable[[LifecycleGraph], TContext]
    store: SessionStore[TSession]
    validate: SessionValidator[TSession]
    parse: Callable[[object], TSession]
    credentials: TCredentials | None = field(default=None, repr=False)
    initial_session: TSession | None = field(default=None, repr=False)
    acquire: SessionAcquirer[TCredentials, TSession, TContext] | None = None
    refresh: SessionRefresher[TSession, TContext] | None = None
    diagnostic_name: str = "session"

    def __post_init__(self) -> None:
        if self.credentials is not None and self.initial_session is not None:
            raise ValueError("credentials and session are mutually exclusive")


class SessionLifecycle[TCredentials, TSession, TContext]:
    """One acquire/validate/refresh/store state machine for every downstream transport."""

    def __init__(self, config: SessionLifecycleConfig[TCredentials, TSession, TContext]) -> None:
        self.config = config
        self._lock = asyncio.Lock()
        self._revision = 0

    @property
    def can_refresh(self) -> bool:
        return self.config.refresh is not None

    async def resolve(
        self,
        graph: LifecycleGraph | None = None,
        operation: str = "operation",
    ) -> SessionRecord[TSession]:
        parent = graph or LifecycleGraph()
        child = parent.enter(
            LifecycleNode(
                id(self),
                operation,
                f"acquire {self.config.diagnostic_name}",
            )
        )
        async with self._lock:
            stored = await self.config.store.load(self.config.key)
            if stored is not None:
                self._revision = max(self._revision, stored.revision.value)
                if self.config.validate(stored.value):
                    return self._record(stored.value, stored.revision)
                if self.config.refresh is not None:
                    return await self._refresh_locked(stored, parent)

            initial = self.config.initial_session
            if initial is not None:
                parsed = self.config.parse(initial)
                if self.config.validate(parsed):
                    return await self._save_next(parsed)

            if self.config.credentials is None or self.config.acquire is None:
                raise SessionCredentialsRequiredError(
                    f"credentials required for {self.config.diagnostic_name}"
                )

            value = await self.config.acquire.acquire(
                self.config.credentials,
                self.config.context_factory(child),
            )
            parsed = self.config.parse(value)
            if not self.config.validate(parsed):
                raise SessionValidationError("acquired session failed validation")
            return await self._save_next(parsed)

    async def adopt(self, value: TSession) -> SessionRecord[TSession]:
        """Validate and persist a session returned by account creation or verification."""

        async with self._lock:
            parsed = self.config.parse(value)
            if not self.config.validate(parsed):
                raise SessionValidationError("adopted session failed validation")
            stored = await self.config.store.load(self.config.key)
            if stored is not None:
                self._revision = max(self._revision, stored.revision.value)
            return await self._save_next(parsed)

    async def refresh(
        self,
        current: StoredSession[TSession],
        graph: LifecycleGraph | None = None,
    ) -> SessionRecord[TSession]:
        async with self._lock:
            return await self._refresh_locked(current, graph or LifecycleGraph())

    async def refresh_revision(
        self,
        expected: SessionRevision,
        graph: LifecycleGraph | None = None,
    ) -> SessionRecord[TSession]:
        """Refresh exactly the selected revision or reuse a newer valid one."""

        async with self._lock:
            current = await self.config.store.load(self.config.key)
            if current is None:
                raise SessionCredentialsRequiredError(
                    f"stored session required to refresh {self.config.diagnostic_name}"
                )
            self._revision = max(self._revision, current.revision.value)
            if current.revision.value > expected.value and self.config.validate(current.value):
                return self._record(current.value, current.revision)
            if current.revision.value < expected.value:
                raise SessionRevisionError(
                    "stored session revision is older than the selected revision"
                )
            return await self._refresh_locked(current, graph or LifecycleGraph())

    async def _refresh_locked(
        self,
        current: StoredSession[TSession],
        graph: LifecycleGraph,
    ) -> SessionRecord[TSession]:
        refresher = self.config.refresh
        if refresher is None:
            raise SessionLifecycleError("session refresher is not configured")
        child = graph.enter(
            LifecycleNode(
                id(self),
                "refresh",
                f"refresh {self.config.diagnostic_name}",
            )
        )
        value = await refresher.refresh(
            current.value,
            self.config.context_factory(child),
        )
        parsed = self.config.parse(value)
        if not self.config.validate(parsed):
            raise SessionValidationError("refreshed session failed validation")
        revision = SessionRevision(current.revision.value + 1)
        self._revision = max(self._revision, revision.value)
        await self.config.store.save(self.config.key, parsed, revision)
        return self._record(parsed, revision)

    async def _save_next(self, value: TSession) -> SessionRecord[TSession]:
        self._revision += 1
        revision = SessionRevision(self._revision)
        await self.config.store.save(self.config.key, value, revision)
        return self._record(value, revision)

    def _record(self, value: TSession, revision: SessionRevision) -> SessionRecord[TSession]:
        return SessionRecord(value, self.config.key, revision)


class MemorySessionStore[TSession]:
    """Task-safe through the owning lifecycle lock; useful for one-process clients/tests."""

    def __init__(self) -> None:
        self._values: dict[str, StoredSession[TSession]] = {}

    async def load(self, key: SessionKey) -> StoredSession[TSession] | None:
        return self._values.get(key.value)

    async def save(
        self,
        key: SessionKey,
        value: TSession,
        revision: SessionRevision,
    ) -> None:
        current = self._values.get(key.value)
        if current is not None and revision.value < current.revision.value:
            raise SessionRevisionError("session revision moved backwards")
        self._values[key.value] = StoredSession(value, revision)

    async def invalidate(
        self,
        key: SessionKey,
        expected: SessionRevision | None = None,
    ) -> None:
        current = self._values.get(key.value)
        if current is None:
            return
        if expected is None or current.revision == expected:
            self._values.pop(key.value, None)


@dataclass(frozen=True, slots=True)
class _SessionSchema[TSession]:
    model: type[TSession]
    expires_field: str | None
    expires: ExpiresAt | None
    clock: Callable[[], datetime]

    @classmethod
    def compile(
        cls,
        model: type[TSession],
        clock: Callable[[], datetime],
    ) -> _SessionSchema[TSession]:
        fields = default_model_adapters().fields(model)
        expires = [
            (field.name, marker)
            for field in fields
            for marker in field.metadata
            if isinstance(marker, ExpiresAt)
        ]
        if len(expires) > 1:
            raise SessionConfigurationError(
                "session model cannot declare multiple ExpiresAt fields"
            )
        return cls(
            model,
            expires[0][0] if expires else None,
            expires[0][1] if expires else None,
            clock,
        )

    def parse(self, value: object) -> TSession:
        return default_model_adapters().load(self.model, value)

    def is_valid(self, value: TSession) -> bool:
        if self.expires_field is None or self.expires is None:
            return True
        expires_at = getattr(value, self.expires_field, None)
        if not isinstance(expires_at, datetime):
            raise TypeError("ExpiresAt field must contain a datetime")
        now = self.clock()
        if expires_at.tzinfo is None or now.tzinfo is None:
            raise TypeError("ExpiresAt and clock values must be timezone-aware")
        return expires_at > now + self.expires.leeway


def session_lifecycle[TCredentials, TSession, TContext](
    session_model: type[TSession],
    *,
    context_factory: Callable[[LifecycleGraph], TContext],
    credentials: TCredentials | None = None,
    session: TSession | None = None,
    service: object | None = None,
    store: SessionStore[TSession] | None = None,
    identity: str | None = None,
    name: str = "session",
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SessionLifecycle[TCredentials, TSession, TContext]:
    """Configure a standalone lifecycle without HTTP placement or response handling."""

    if credentials is not None and session is not None:
        raise ValueError("credentials and session are mutually exclusive")
    schema = _SessionSchema.compile(session_model, clock)
    acquire = getattr(service, "acquire", None)
    refresh = getattr(service, "refresh", None)
    if credentials is not None and not callable(acquire):
        raise SessionConfigurationError(
            "session service with acquire() is required when credentials are provided"
        )
    return SessionLifecycle(
        SessionLifecycleConfig(
            key=SessionKey(f"session:{identity or 'client'}"),
            context_factory=context_factory,
            store=store or MemorySessionStore[TSession](),
            validate=schema.is_valid,
            parse=schema.parse,
            credentials=credentials,
            initial_session=session,
            acquire=(
                cast(SessionAcquirer[TCredentials, TSession, TContext], service)
                if callable(acquire)
                else None
            ),
            refresh=(
                cast(SessionRefresher[TSession, TContext], service) if callable(refresh) else None
            ),
            diagnostic_name=name,
        )
    )


__all__ = [
    "BridgedSessionAdopter",
    "ExpiresAt",
    "LifecycleGraph",
    "LifecycleNode",
    "MemorySessionStore",
    "RefreshToken",
    "SessionAcquirer",
    "SessionAdopter",
    "SessionBridge",
    "SessionConfigurationError",
    "SessionCredentialsRequiredError",
    "SessionKey",
    "SessionLifecycle",
    "SessionLifecycleConfig",
    "SessionLifecycleCycleError",
    "SessionLifecycleError",
    "SessionRecord",
    "SessionRefresher",
    "SessionRevision",
    "SessionRevisionError",
    "SessionStore",
    "SessionValidationError",
    "SessionValidator",
    "StoredSession",
    "session_lifecycle",
]
