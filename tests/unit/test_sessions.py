from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from eazy_sdk import PlanError
from eazy_sdk.auth import AuthCredentialsRequiredError, BearerScheme
from eazy_sdk.ext import (
    MemorySessionStore,
    SessionAuth,
    SessionKey,
    SessionProvider,
    SessionRevision,
    StoredSession,
)

pytestmark = pytest.mark.unit


@dataclass
class Acquirer:
    value: object = "valid-acquired"
    error: Exception | None = None
    calls: int = 0

    async def acquire(self, credentials: str, context: object) -> object:
        self.calls += 1
        assert credentials == "credentials"
        if self.error is not None:
            raise self.error
        return self.value


@dataclass
class Refresher:
    value: str = "valid-refreshed"
    error: Exception | None = None
    calls: int = 0

    async def refresh(self, session: str, context: object) -> str:
        self.calls += 1
        assert session.startswith("valid-")
        if self.error is not None:
            raise self.error
        return self.value


def config(
    store: MemorySessionStore[str],
    *,
    credentials: str | None = None,
    initial_session: str | None = None,
    acquire: object | None = None,
    refresh: object | None = None,
) -> SessionAuth[str, str, object]:
    return SessionAuth(
        scheme=BearerScheme("session"),
        key=SessionKey("account:test"),
        sdk_factory=lambda _graph: object(),
        store=store,
        validate=lambda value: value.startswith("valid-"),
        credentials=credentials,
        initial_session=initial_session,
        acquire=acquire,  # type: ignore[arg-type]
        refresh=refresh,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_valid_initial_session_is_saved_and_reused_without_acquisition() -> None:
    store: MemorySessionStore[str] = MemorySessionStore()
    acquirer = Acquirer()
    provider = SessionProvider(config(store, initial_session="valid-initial", acquire=acquirer))
    first = await provider.resolve()
    second = await provider.resolve()
    assert first.value == second.value == "valid-initial"
    assert first.execution.revision == second.execution.revision == SessionRevision(1)
    assert acquirer.calls == 0


@pytest.mark.asyncio
async def test_invalid_stored_session_is_replaced_with_a_newer_revision() -> None:
    store: MemorySessionStore[str] = MemorySessionStore()
    key = SessionKey("account:test")
    await store.save(key, "expired", SessionRevision(4))
    acquirer = Acquirer()
    provider = SessionProvider(config(store, credentials="credentials", acquire=acquirer))
    resolved = await provider.resolve()
    assert resolved.value == "valid-acquired"
    assert resolved.execution.revision == SessionRevision(5)
    assert await store.load(key) == StoredSession("valid-acquired", SessionRevision(5))
    assert acquirer.calls == 1


@pytest.mark.asyncio
async def test_missing_credentials_for_an_invalid_session_fails_without_overwriting_store() -> None:
    store: MemorySessionStore[str] = MemorySessionStore()
    key = SessionKey("account:test")
    await store.save(key, "expired", SessionRevision(2))
    provider = SessionProvider(config(store))
    with pytest.raises(AuthCredentialsRequiredError, match="credentials required"):
        await provider.resolve()
    assert await store.load(key) == StoredSession("expired", SessionRevision(2))


@pytest.mark.asyncio
async def test_acquisition_failure_or_invalid_result_does_not_replace_stored_state() -> None:
    store: MemorySessionStore[str] = MemorySessionStore()
    key = SessionKey("account:test")
    await store.save(key, "expired", SessionRevision(2))

    failed = SessionProvider(
        config(
            store,
            credentials="credentials",
            acquire=Acquirer(error=RuntimeError("login failed")),
        )
    )
    with pytest.raises(RuntimeError, match="login failed"):
        await failed.resolve()
    assert await store.load(key) == StoredSession("expired", SessionRevision(2))

    invalid = SessionProvider(
        config(store, credentials="credentials", acquire=Acquirer(value="expired-again"))
    )
    with pytest.raises(PlanError, match="failed validation"):
        await invalid.resolve()
    assert await store.load(key) == StoredSession("expired", SessionRevision(2))


@pytest.mark.asyncio
async def test_refresh_success_and_failure_have_revision_safe_persistence() -> None:
    store: MemorySessionStore[str] = MemorySessionStore()
    key = SessionKey("account:test")
    current = StoredSession("valid-current", SessionRevision(3))
    await store.save(key, current.value, current.revision)
    provider = SessionProvider(config(store, refresh=Refresher()))
    refreshed = await provider.refresh(current)
    assert refreshed.value == "valid-refreshed"
    assert refreshed.execution.revision == SessionRevision(4)
    assert await store.load(key) == StoredSession("valid-refreshed", SessionRevision(4))

    failing = SessionProvider(
        config(store, refresh=Refresher(error=RuntimeError("refresh failed")))
    )
    with pytest.raises(RuntimeError, match="refresh failed"):
        await failing.refresh(StoredSession("valid-refreshed", SessionRevision(4)))
    assert await store.load(key) == StoredSession("valid-refreshed", SessionRevision(4))


@pytest.mark.asyncio
async def test_refresh_without_a_refresher_is_explicitly_rejected() -> None:
    provider = SessionProvider(config(MemorySessionStore()))
    with pytest.raises(PlanError, match="refresher is not configured"):
        await provider.refresh(StoredSession("valid-current", SessionRevision(1)))


@pytest.mark.asyncio
async def test_concurrent_refresh_of_one_execution_is_singleflight_and_revision_safe() -> None:
    store: MemorySessionStore[str] = MemorySessionStore()
    refresher = Refresher()
    provider = SessionProvider(config(store, initial_session="valid-current", refresh=refresher))
    selected = await provider.resolve()

    first, second = await asyncio.gather(
        provider.refresh_execution(selected.execution),
        provider.refresh_execution(selected.execution),
    )

    assert first.value == second.value == "valid-refreshed"
    assert first.execution.revision == second.execution.revision == SessionRevision(2)
    assert refresher.calls == 1
    assert await store.load(SessionKey("account:test")) == StoredSession(
        "valid-refreshed", SessionRevision(2)
    )


@pytest.mark.asyncio
async def test_store_invalidation_is_idempotent_and_revision_guarded() -> None:
    store: MemorySessionStore[str] = MemorySessionStore()
    key = SessionKey("account:test")
    await store.save(key, "valid-current", SessionRevision(2))
    await store.invalidate(key, SessionRevision(1))
    assert await store.load(key) == StoredSession("valid-current", SessionRevision(2))
    await store.invalidate(key, SessionRevision(2))
    await store.invalidate(key, SessionRevision(2))
    assert await store.load(key) is None
