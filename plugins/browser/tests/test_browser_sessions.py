"""Браузерная сессия в хранилище аккаунтов `eazy-sdk`.

Проверяется главное обещание слияния: хранилище **транспортно-нейтрально**, то есть
браузеру от него нужно то же, что HTTP, и ради браузера его не приходится менять.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from eazy_sdk_accounts.storage import AccountWorkspace, MemoryStorage, SessionData
from eazy_sdk_browser import AsyncBrowserClient, BrowserLogin, BrowserLoginContext
from eazy_sdk_browser.integrations.accounts import (
    BROWSER_SCHEME,
    KEY_FIELD,
    REVISION_FIELD,
    BrowserSessions,
    from_session_data,
    to_session_data,
)
from eazy_sdk_browser.state import BrowserCookie, BrowserState, Origin, require_state
from eazy_sdk_browser.testing import FakeDriver, StatefulFakeDriver, StatelessLiar

from eazy_sdk.auth.session import SessionKey, SessionRevision, StoredSession
from eazy_sdk.handlers import CapabilityMismatchError

pytestmark = pytest.mark.unit

EXPIRES = datetime(2030, 1, 1, tzinfo=UTC)

SAVED = BrowserState(
    cookies=(
        BrowserCookie(
            name="sid",
            value="abc123",
            domain=".mail.example",
            path="/",
            expires_at=EXPIRES,
            secure=True,
            http_only=True,
            same_site="Lax",
        ),
        BrowserCookie(name="consent", value="1", domain=".mail.example"),
    ),
    origins=(Origin(origin="https://mail.example", items=(("theme", "dark"),)),),
)


def workspace() -> AccountWorkspace:
    return AccountWorkspace(MemoryStorage(record_events=True))


# --- доступ к возможности -------------------------------------------------------------------


def test_state_requires_capability() -> None:
    """Драйверу без возможности сценарий отказывает до первого действия."""
    with pytest.raises(CapabilityMismatchError):
        require_state(FakeDriver())


def test_state_liar_is_adapter_error() -> None:
    """Профиль обещал, методов нет: виноват адаптер, и это видно по типу ошибки."""
    with pytest.raises(TypeError, match="export_state"):
        require_state(StatelessLiar())


# --- перевод состояния в запись хранилища и обратно ------------------------------------------


def test_round_trip_keeps_every_attribute() -> None:
    """Состояние переживает запись в хранилище без потерь."""
    assert from_session_data(to_session_data(SAVED)) == SAVED


def test_flat_cookies_stay_readable_for_http() -> None:
    """Плоские куки записи — та самая проекция, которую понимает HTTP-клиент."""
    data = to_session_data(SAVED)
    assert data.scheme == BROWSER_SCHEME
    assert data.cookies == {"sid": "abc123", "consent": "1"}


def test_http_session_needs_domain_to_reach_browser() -> None:
    """Запись от HTTP-SDK браузеру годится, только если сказать домен.

    Это и есть цена того, что `SessionData.cookies` — пара «имя-значение»:
    домен, путь и срок в ней не помещаются, и вызывающий добавляет их руками.
    """
    http_written = SessionData(scheme="cookie", cookies={"sid": "abc123"})

    assert from_session_data(http_written).is_empty()

    restored = from_session_data(http_written, domain=".mail.example")
    assert restored.cookies == (BrowserCookie(name="sid", value="abc123", domain=".mail.example"),)


# --- работа через хранилище -----------------------------------------------------------------


async def test_save_and_load_through_storage() -> None:
    """Сессия сохраняется за аккаунтом и возвращается целиком — без драйвера."""
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("user@mail.example", provider="mail")

    await sessions.save(account, SAVED)

    assert await sessions.load(account) == SAVED


async def test_load_without_saved_session() -> None:
    """Сохранять было нечего — первый контекст открывается без `storage_state`."""
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("new@mail.example", provider="mail")

    assert await sessions.load(account) is None


async def test_expired_session_is_not_offered() -> None:
    """Протухшую сессию восстанавливать бессмысленно: сервис уведёт на вход."""
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("old@mail.example", provider="mail")
    await sessions.save(account, SAVED, expires_at=datetime.now(UTC) - timedelta(minutes=1))

    assert await sessions.load(account) is None


async def test_forget_invalidates_session() -> None:
    """Сессия объявлена негодной — хранилище больше её не отдаёт."""
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("bye@mail.example", provider="mail")
    await sessions.save(account, SAVED)

    await sessions.forget(account)

    assert await sessions.load(account) is None


async def test_saving_session_writes_account_history() -> None:
    """Хранилище само записывает, что аккаунт авторизовался: событие не пишет SDK."""
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("log@mail.example", provider="mail")

    await sessions.save(account, SAVED)

    timeline = await sessions.workspace.history.timeline(account)
    assert [event.type for event in timeline] == ["account.authorized", "account.created"]


# --- хранилище для жизненного цикла входа (B5.2) ---------------------------------------------

KEY = SessionKey("browser:user@mail.example")


async def test_store_keeps_the_revision_and_the_key_in_session_meta() -> None:
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("rev@mail.example", provider="mail")
    store = sessions.store(account)

    await store.save(KEY, SAVED, SessionRevision(3))

    record = await sessions.workspace.sessions.active(account, label=BROWSER_SCHEME)
    assert record is not None
    assert (record.meta[REVISION_FIELD], record.meta[KEY_FIELD]) == (3, KEY.value)
    assert await store.load(KEY) == StoredSession(SAVED, SessionRevision(3))


async def test_store_invalidates_only_the_revision_it_was_asked_about() -> None:
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("inv@mail.example", provider="mail")
    store = sessions.store(account)
    await store.save(KEY, SAVED, SessionRevision(3))

    await store.invalidate(KEY, SessionRevision(2))
    survived = await store.load(KEY)
    await store.invalidate(KEY, SessionRevision(3))

    assert survived is not None
    assert await store.load(KEY) is None


async def test_store_does_not_serve_a_session_saved_under_another_key() -> None:
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("key@mail.example", provider="mail")
    store = sessions.store(account)

    await store.save(SessionKey("browser:someone-else"), SAVED, SessionRevision(1))

    assert await store.load(KEY) is None


async def test_login_persists_its_session_into_the_account_workspace() -> None:
    """Сессия контекста по жизненному циклу ядра пишет сессию за аккаунтом — и историю тоже."""
    sessions = BrowserSessions(workspace())
    account = await sessions.workspace.accounts.get_or_create("in@mail.example", provider="mail")

    class Issue:
        async def acquire(self, credentials: str, context: BrowserLoginContext) -> BrowserState:
            _ = credentials
            # Вход кладёт сессию в контекст страницы — как настоящая форма входа.
            cast("StatefulFakeDriver", context.client.driver).state = SAVED
            return SAVED

    login = BrowserLogin(
        service=Issue(),
        cookies=("sid",),
        clock=lambda: EXPIRES - timedelta(days=1),
    )
    mail = login.session("secret", store=sessions.store(account), identity="in@mail.example")

    state = await mail.state(AsyncBrowserClient(StatefulFakeDriver(), session=mail))

    assert state == SAVED
    assert await sessions.load(account) == SAVED
    timeline = await sessions.workspace.history.timeline(account)
    assert [event.type for event in timeline] == ["account.authorized", "account.created"]
