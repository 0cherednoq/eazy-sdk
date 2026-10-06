"""Сессия одного контекста браузера: один объект на все его вкладки.

Сессия лежит в контексте — в его куках, — поэтому её жизненный цикл принадлежит объекту
с временем жизни контекста. Не вкладки: пять вкладок дали бы пять входов в общий
контекст. И не аккаунта: объекту пришлось бы помнить, в какой из контекстов какая
ревизия уже положена, а это решение «что делать, когда контекстов несколько», которое
принадлежит слою выше.

    mail = MAIL_LOGIN.session(credentials, store=sessions.store(account), identity="ada")
    async with (
        AsyncBrowserClient(PlaywrightDriver(page1), base_url=BASE, session=mail) as a,
        AsyncBrowserClient(PlaywrightDriver(page2), base_url=BASE, session=mail) as b,
    ):
        await MailPortal(a).send(...)       # один вход на контекст

Внутри — `SessionLifecycle` ядра (хранилище, проверка, повторный вход по ревизии) и свой
лок на шаг «посмотреть в контекст → разрешить → положить куки». Вход идёт на странице
того клиента, который первым упёрся в отсутствие сессии. В контекст пишутся только куки
и только когда их там нет: контекст, созданный со `storage_state`, и контекст, где сайт
сам ротировал куку, записи не получают — иначе свежую куку затёрла бы старая из хранилища.

Два контекста одного аккаунта — два объекта **с общим `store`**: согласует их только
хранилище ревизий, как два `Auth` в HTTP. Объект, которому принесли клиента из другого
контекста, отказывает: это нарушение правила «один объект на контекст», а не повод
решать, как делить сессию между контекстами.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from eazy_sdk.auth.session import (
    MemorySessionStore,
    SessionCredentialsRequiredError,
    SessionKey,
    SessionLifecycle,
    SessionLifecycleConfig,
)
from eazy_sdk_browser.login import BrowserSessionError
from eazy_sdk_browser.state import BrowserState, StateAware, require_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from eazy_sdk.auth.session import (
        LifecycleGraph,
        SessionRecord,
        SessionRevision,
        SessionStore,
    )
    from eazy_sdk_browser.client import AsyncBrowserClient
    from eazy_sdk_browser.login import BrowserLogin

_ENTRANCE: ContextVar[AsyncBrowserClient] = ContextVar("browser_session_entrance")
"""Клиент, на странице которого идёт вход, — на время одного `resolve`/`refresh_revision`.

Фабрика контекста у жизненного цикла одна на объект, а страница входа у каждого вызова
своя. Под локом жизненного цикла в один момент входит один вызов, поэтому значение
однозначно."""


class BrowserSession[TCredentials]:
    """`SessionSource` клиента без пула: жизненный цикл сессии одного контекста.

    Строится `BrowserLogin.session(...)`. `applied` — ревизия, уже лежащая в контексте;
    `applied_by_login_here` — её добыл вход на странице этого контекста, а не хранилище.
    """

    def __init__(
        self,
        login: BrowserLogin[TCredentials],
        credentials: TCredentials,
        *,
        store: SessionStore[BrowserState] | None = None,
        identity: str = "client",
    ) -> None:
        self._login = login
        self._lifecycle: SessionLifecycle[TCredentials, BrowserState, AsyncBrowserClient] = (
            SessionLifecycle(
                SessionLifecycleConfig(
                    key=SessionKey(f"browser:{identity}"),
                    context_factory=_entrance,
                    store=MemorySessionStore[BrowserState]() if store is None else store,
                    validate=login.validate,
                    parse=_state_of,
                    credentials=credentials,
                    acquire=_Acquire(login, self._signed_in_here),
                    refresh=_Refresh(login, credentials, self._signed_in_here),
                    diagnostic_name=f"browser session {identity}",
                )
            )
        )
        self._lock = asyncio.Lock()
        self._context: object | None = None
        self._logged_in = False
        self.applied: SessionRevision | None = None
        self.applied_by_login_here = False

    @property
    def login(self) -> BrowserLogin[TCredentials]:
        return self._login

    def is_expired(self, error: BaseException) -> bool:
        return self._login.is_expired(error)

    async def ensure(self, client: AsyncBrowserClient) -> SessionRevision:
        """Сессия в контексте: уже положенная, из контекста, из хранилища или входом."""
        page = self._page_of(client)
        async with self._lock:
            if self.applied is not None:
                return self.applied
            present = await page.export_state()
            if self._login.validate(present):
                return await self._adopt_context(present)
            record = await self._resolved(client)
            if not self.applied_by_login_here:
                await page.add_cookies(record.value.cookies)
            return record.revision

    async def renew(
        self, client: AsyncBrowserClient, rejected: SessionRevision
    ) -> SessionRevision | None:
        """Сервис отверг `rejected`: войти заново — или взять ревизию, которую уже обновили."""
        page = self._page_of(client)
        async with self._lock:
            if self.applied is not None and self.applied.value > rejected.value:
                return self.applied
            record = await self._refreshed(client, rejected)
            if not self.applied_by_login_here:
                # Ревизию добыл соседний объект того же аккаунта: в этом контексте её нет.
                await page.add_cookies(record.value.cookies)
            return record.revision

    async def state(self, client: AsyncBrowserClient) -> BrowserState:
        """Разрешить сессию без операции — для HTTP-моста и `storage_state` следующего контекста."""
        await self.ensure(client)
        return await self._page_of(client).export_state()

    def _page_of(self, client: AsyncBrowserClient) -> StateAware:
        """Драйвер, умеющий сессию, — и из того же контекста, что и первый клиент."""
        page = require_state(client.driver)
        key = page.context_key()
        if self._context is None:
            self._context = key
        elif self._context != key:
            msg = (
                "BrowserSession serves one browser context; a client from another context "
                "needs its own login.session(...) with the same store"
            )
            raise BrowserSessionError(msg)
        return page

    async def _adopt_context(self, present: BrowserState) -> SessionRevision:
        """В контексте уже годная сессия: не входить и не писать, только узнать её ревизию."""
        stored = await self._lifecycle.config.store.load(self._lifecycle.config.key)
        if stored is not None and self._login.validate(stored.value):
            self._apply(stored.revision, by_login_here=False)
            return stored.revision
        record = await self._lifecycle.adopt(present)
        self._apply(record.revision, by_login_here=False)
        return record.revision

    async def _resolved(self, client: AsyncBrowserClient) -> SessionRecord[BrowserState]:
        self._logged_in = False
        token = _ENTRANCE.set(client)
        try:
            record = await self._lifecycle.resolve(operation="ensure")
        finally:
            _ENTRANCE.reset(token)
        self._apply(record.revision, by_login_here=self._logged_in)
        return record

    async def _refreshed(
        self, client: AsyncBrowserClient, rejected: SessionRevision
    ) -> SessionRecord[BrowserState]:
        self._logged_in = False
        token = _ENTRANCE.set(client)
        try:
            try:
                record = await self._lifecycle.refresh_revision(rejected)
            except SessionCredentialsRequiredError:
                # Хранилище забыло сессию (её объявили негодной снаружи) — входим с нуля.
                record = await self._lifecycle.resolve(operation="renew")
        finally:
            _ENTRANCE.reset(token)
        self._apply(record.revision, by_login_here=self._logged_in)
        return record

    def _apply(self, revision: SessionRevision, *, by_login_here: bool) -> None:
        """Признак входа копируется только вместе с новой ревизией — упавший вход его не оставит."""
        self.applied = revision
        self.applied_by_login_here = by_login_here

    def _signed_in_here(self) -> None:
        """Зовут адаптеры жизненного цикла — после успешного входа, не до."""
        self._logged_in = True


@dataclass(frozen=True, slots=True)
class _Acquire[TCredentials]:
    """`acquire` жизненного цикла — вход через `BrowserLogin.sign_in`: путь входа один."""

    login: BrowserLogin[TCredentials]
    signed_in: Callable[[], None]

    async def acquire(self, credentials: TCredentials, context: AsyncBrowserClient) -> BrowserState:
        state = await self.login.sign_in(context, credentials)
        self.signed_in()
        return state


@dataclass(frozen=True, slots=True)
class _Refresh[TCredentials]:
    """`refresh` жизненного цикла — `BrowserLogin.refresh`: свой refresh сервиса или вход."""

    login: BrowserLogin[TCredentials]
    credentials: TCredentials = field(repr=False)
    signed_in: Callable[[], None]

    async def refresh(self, session: BrowserState, context: AsyncBrowserClient) -> BrowserState:
        state = await self.login.refresh(context, self.credentials, session)
        self.signed_in()
        return state


def _entrance(graph: LifecycleGraph) -> AsyncBrowserClient:
    """Клиент для входа: страница того, кто упёрся, но без сессии — иначе вход зациклится."""
    _ = graph
    return _ENTRANCE.get().without_session()


def _state_of(value: object) -> BrowserState:
    if not isinstance(value, BrowserState):
        msg = f"browser session must be BrowserState, got {type(value).__name__}"
        raise TypeError(msg)
    return value


__all__ = ["BrowserSession"]
