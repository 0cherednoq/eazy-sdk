"""Сессия одного контекста: `BrowserSession` — один объект на все вкладки контекста.

Вкладки одного контекста — фейки с общим `FakeContext`: куки, которые положил вход на
одной, видны остальным, а каждая запись кук снаружи (`add_cookies`) попадает в журнал
`cookie_writes`. Сервер почты — общий на все контексты: он помнит, какую `sid` выдал
последней, и пускает в ящик только с ней.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast

import pytest
from eazy_sdk_browser import (
    AsyncBrowserApi,
    AsyncBrowserClient,
    Browser,
    BrowserCallOptions,
    BrowserClientConfig,
    BrowserCookie,
    BrowserDeclarationError,
    BrowserLogin,
    BrowserLoginContext,
    BrowserOperation,
    BrowserSession,
    BrowserSessionError,
    BrowserState,
    Element,
    Failure,
    LoadState,
    PageError,
    css,
    url,
)
from eazy_sdk_browser.testing import FakeContext, FakeDriver, StatefulFakeDriver

from eazy_sdk import op
from eazy_sdk.auth.session import (
    MemorySessionStore,
    SessionKey,
    SessionRevision,
    StoredSession,
)
from eazy_sdk.handlers import CapabilityMismatchError

pytestmark = pytest.mark.unit

BASE = "https://mail.example"
LOGIN_URL = f"{BASE}/login"
INBOX_URL = f"{BASE}/inbox"
NOW = datetime(2030, 1, 1, tzinfo=UTC)
KEY = SessionKey("browser:ada")
FAST = BrowserCallOptions(timeout=0.05, element_timeout=0.05)
LOGIN_FORM = {'input[name="user"]', 'input[name="password"]', "button.login"}


class SessionExpiredError(PageError):
    """Почта увела на вход."""


def sid(value: str, *, lifetime: timedelta = timedelta(days=1)) -> BrowserCookie:
    return BrowserCookie(name="sid", value=value, domain=".mail.example", expires_at=NOW + lifetime)


def holding(*cookies: BrowserCookie) -> FakeContext:
    return FakeContext(BrowserState(cookies=cookies))


@dataclass(frozen=True, slots=True)
class Credentials:
    user: str
    password: str = field(repr=False)


ADA = Credentials("ada", "secret")


# --- почта: вход и ящик ---------------------------------------------------------------------


class LoginForm:
    user: Annotated[Element, css('input[name="user"]')]
    password: Annotated[Element, css('input[name="password"]')]
    submit: Annotated[Element, css("button.login")]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenLogin(BrowserOperation[None, None]):
    __browser__ = Browser.goto("/login", at=css("button.login"))


@dataclass(frozen=True, slots=True, kw_only=True)
class SignIn(BrowserOperation[LoginForm, None]):
    __browser__ = Browser.act(LoginForm, at=css("button.login"))

    user: str
    password: str

    async def act(self, content: LoginForm) -> None:
        await content.user.fill(self.user)
        await content.password.fill(self.password)
        await content.submit.click()


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenInbox(BrowserOperation[None, None]):
    __browser__ = Browser.goto("/inbox", at=css("div.inbox"))


class Entrance(AsyncBrowserApi):
    open_login = op(OpenLogin)
    sign_in = op(SignIn)


class Mail(AsyncBrowserApi):
    errors = (Failure(when=url.contains("/login"), exception=SessionExpiredError),)

    open_inbox = op(OpenInbox)


@dataclass
class MailServer:
    """Сервер почты, общий для всех контекстов: помнит последнюю выданную `sid`."""

    knows: str | None = None
    issued: int = 0

    def issue(self) -> str:
        self.issued += 1
        self.knows = f"s{self.issued}"
        return self.knows


@dataclass
class MailTab(StatefulFakeDriver):
    """Вкладка почты: вход кладёт выданную куку в контекст, ящик пускает только с ней."""

    server: MailServer = field(default_factory=MailServer)

    def on_click(self, selector: str) -> None:
        if selector == "button.login":
            self.context.put_cookies((sid(self.server.issue()),))

    async def goto(self, url: str, *, wait: LoadState = "load", within: float) -> None:
        self.redirects = {} if self._known() else {INBOX_URL: LOGIN_URL}
        await super().goto(url, wait=wait, within=within)

    def _known(self) -> bool:
        return self.server.knows is not None and any(
            cookie.name == "sid" and cookie.value == self.server.knows
            for cookie in self.context.state.cookies
        )


def tab(context: FakeContext, server: MailServer) -> MailTab:
    return MailTab(
        pages={LOGIN_URL: set(LOGIN_FORM), INBOX_URL: {"div.inbox"}},
        context=context,
        server=server,
    )


@dataclass
class EntranceLogin:
    """Сервис входа: операции страницы входа через клиент без сессии."""

    calls: int = 0
    clients: list[AsyncBrowserClient] = field(default_factory=list)
    failures: int = 0
    """Сколько первых входов упадёт."""

    async def acquire(self, credentials: Credentials, context: BrowserLoginContext) -> BrowserState:
        self.calls += 1
        self.clients.append(context.client)
        if self.failures:
            self.failures -= 1
            msg = "капча"
            raise RuntimeError(msg)
        entrance = Entrance(context.client)
        await entrance.open_login(options=FAST)
        await entrance.sign_in(user=credentials.user, password=credentials.password, options=FAST)
        return await context.export_state()


@dataclass
class ForgedLogin:
    """Сервис, который «входит» с кукой, которую сервер не выдавал."""

    cookie: str = "sid"
    calls: int = 0

    async def acquire(self, credentials: Credentials, context: BrowserLoginContext) -> BrowserState:
        _ = credentials
        self.calls += 1
        state = BrowserState(cookies=(BrowserCookie(self.cookie, "forged", ".mail.example"),))
        cast("StatefulFakeDriver", context.client.driver).context.put_cookies(state.cookies)
        return state


@dataclass
class CountingStore:
    """Хранилище в памяти, которое считает чтения."""

    inner: MemorySessionStore[BrowserState] = field(
        default_factory=MemorySessionStore[BrowserState]
    )
    loads: int = 0

    async def load(self, key: SessionKey) -> StoredSession[BrowserState] | None:
        self.loads += 1
        return await self.inner.load(key)

    async def save(self, key: SessionKey, value: BrowserState, revision: SessionRevision) -> None:
        await self.inner.save(key, value, revision)

    async def invalidate(self, key: SessionKey, expected: SessionRevision | None = None) -> None:
        await self.inner.invalidate(key, expected)


def login_of(service: object) -> BrowserLogin[Credentials]:
    return BrowserLogin(
        service=cast("Any", service),
        cookies=("sid",),
        expired=(SessionExpiredError,),
        clock=lambda: NOW,
    )


def session_of(service: object, store: object | None = None) -> BrowserSession[Credentials]:
    return login_of(service).session(ADA, store=cast("Any", store), identity="ada")


def client(driver: FakeDriver, session: object, **config: Any) -> AsyncBrowserClient:
    return AsyncBrowserClient(
        driver,
        base_url=BASE,
        session=cast("Any", session),
        config=BrowserClientConfig(**config),
    )


async def stored(value: str, revision: int = 4) -> MemorySessionStore[BrowserState]:
    store = MemorySessionStore[BrowserState]()
    await store.save(KEY, BrowserState(cookies=(sid(value),)), SessionRevision(revision))
    return store


# --- один объект на контекст -----------------------------------------------------------------


async def test_one_session_object_serves_every_tab_of_its_context() -> None:
    """Три вкладки, параллельные первые операции: один вход, и куки не пишутся — вход их положил."""
    context, server, service = FakeContext(), MailServer(), EntranceLogin()
    mail = session_of(service)
    tabs = [tab(context, server) for _ in range(3)]

    await asyncio.gather(*(Mail(client(each, mail)).open_inbox(options=FAST) for each in tabs))

    assert service.calls == 1
    assert context.cookie_writes == []
    assert [each.url for each in tabs] == [INBOX_URL] * 3


async def test_stored_live_session_is_reused_without_signing_in() -> None:
    """Годная сессия в хранилище, контекст пуст: ни одного входа и ровно одна запись кук."""
    context, service = FakeContext(), EntranceLogin()
    mail = session_of(service, await stored("s7"))
    tabs = [tab(context, MailServer(knows="s7")) for _ in range(3)]

    await asyncio.gather(*(Mail(client(each, mail)).open_inbox(options=FAST) for each in tabs))

    assert service.calls == 0
    assert context.cookie_writes == [(sid("s7"),)]
    assert mail.applied == SessionRevision(4)


async def test_ensure_skips_the_write_when_the_context_already_holds_a_valid_session() -> None:
    """Сайт ротировал куку, а в хранилище старая: свежую не затирают, ревизия — хранилища."""
    context = holding(sid("rotated"))
    mail = session_of(EntranceLogin(), await stored("s7"))

    await Mail(client(tab(context, MailServer(knows="rotated")), mail)).open_inbox(options=FAST)

    assert context.cookie_writes == []
    assert context.state.cookies == (sid("rotated"),)
    assert mail.applied == SessionRevision(4)


@pytest.mark.parametrize(
    "present",
    [(), (sid("s7", lifetime=-timedelta(minutes=1)),)],
    ids=["no-cookie", "expired-cookie"],
)
async def test_ensure_writes_when_the_context_session_is_not_valid(
    present: tuple[BrowserCookie, ...],
) -> None:
    context = holding(*present)
    mail = session_of(EntranceLogin(), await stored("s7"))

    await Mail(client(tab(context, MailServer(knows="s7")), mail)).open_inbox(options=FAST)

    assert context.cookie_writes == [(sid("s7"),)]
    assert mail.applied == SessionRevision(4)


async def test_context_with_a_valid_session_is_adopted_without_signing_in() -> None:
    """Контекст создан со `storage_state`, хранилище пусто: сессию принимают, а не входят."""
    context, service, store = (
        holding(sid("s7")),
        EntranceLogin(),
        MemorySessionStore[BrowserState](),
    )
    mail = session_of(service, store)

    await Mail(client(tab(context, MailServer(knows="s7")), mail)).open_inbox(options=FAST)

    saved = await store.load(KEY)
    assert service.calls == 0
    assert context.cookie_writes == []
    assert saved == StoredSession(BrowserState(cookies=(sid("s7"),)), SessionRevision(1))


async def test_failed_sign_in_leaves_no_login_here_mark() -> None:
    service = EntranceLogin(failures=1)
    mail = session_of(service)
    portal = Mail(client(tab(FakeContext(), MailServer()), mail))

    with pytest.raises(RuntimeError, match="капча"):
        await portal.open_inbox(options=FAST)

    assert (mail.applied, mail.applied_by_login_here) == (None, False)
    await portal.open_inbox(options=FAST)
    assert service.calls == 2
    assert (mail.applied, mail.applied_by_login_here) == (SessionRevision(1), True)


async def test_ensure_fast_path_does_not_touch_the_store() -> None:
    store = CountingStore()
    portal = Mail(client(tab(FakeContext(), MailServer()), session_of(EntranceLogin(), store)))
    await portal.open_inbox(options=FAST)
    loads = store.loads

    await portal.open_inbox(options=FAST)
    await portal.open_inbox(options=FAST)

    assert store.loads == loads


async def test_sign_in_page_is_the_client_that_hit_the_missing_session() -> None:
    """Вход идёт на странице того, кто взял лок, — с его базой и конфигурацией, без сессии."""
    context, server, service = FakeContext(), MailServer(), EntranceLogin()
    mail = session_of(service)
    clients = [
        client(tab(context, server), mail, element_timeout=0.5),
        client(tab(context, server), mail, element_timeout=0.7),
    ]

    await asyncio.gather(*(Mail(each).open_inbox(options=FAST) for each in clients))

    [entrance] = service.clients
    hit = next(each for each in clients if each.driver is entrance.driver)
    assert (entrance.base_url, entrance.config, entrance.session) == (BASE, hit.config, None)


async def test_lifecycle_enters_only_through_login_sign_in_and_refresh() -> None:
    """Сервис вернул сессию без объявленной куки: отказывает `BrowserLogin.sign_in`, а не ядро."""
    store = MemorySessionStore[BrowserState]()
    portal = Mail(client(tab(FakeContext(), MailServer()), session_of(ForgedLogin("other"), store)))

    with pytest.raises(BrowserSessionError, match="sign-in finished"):
        await portal.open_inbox(options=FAST)

    assert await store.load(KEY) is None


async def test_session_requires_a_declared_cookie() -> None:
    login = BrowserLogin(service=EntranceLogin())

    with pytest.raises(BrowserDeclarationError, match="cookies"):
        login.session(ADA)

    state = await login.sign_in(
        AsyncBrowserClient(tab(FakeContext(), MailServer()), base_url=BASE), ADA
    )
    assert state == BrowserState(cookies=(sid("s1"),))


# --- повторный вход -------------------------------------------------------------------------


async def test_declared_expiry_signs_in_again_and_repeats_the_operation_once() -> None:
    """Сервер забыл сессию из хранилища: вход на этой странице, повтор, соседу — готовая ревизия."""
    context, server, service, store = FakeContext(), MailServer(), EntranceLogin(), CountingStore()
    await store.save(KEY, BrowserState(cookies=(sid("s7"),)), SessionRevision(4))
    mail = session_of(service, store)
    first, neighbour = client(tab(context, server), mail), client(tab(context, server), mail)

    await Mail(first).open_inbox(options=FAST)

    assert (service.calls, mail.applied) == (1, SessionRevision(5))
    assert context.cookie_writes == [(sid("s7"),)], "запись одна — из хранилища, до отказа"
    loads = store.loads
    assert await mail.renew(neighbour, SessionRevision(4)) == SessionRevision(5)
    assert (store.loads, service.calls, len(context.cookie_writes)) == (loads, 1, 1)


async def test_renew_writes_a_revision_obtained_elsewhere() -> None:
    """Соседний контекст вошёл заново: этот берёт его ревизию из хранилища и пишет куки."""
    server, service, store = MailServer(), EntranceLogin(), MemorySessionStore[BrowserState]()
    here, there = FakeContext(), FakeContext()
    mail_here, mail_there = session_of(service, store), session_of(service, store)
    client_here, client_there = (
        client(tab(here, server), mail_here),
        client(tab(there, server), mail_there),
    )
    await Mail(client_here).open_inbox(options=FAST)
    await Mail(client_there).open_inbox(options=FAST)
    await mail_there.renew(client_there, SessionRevision(1))

    await Mail(client_here).open_inbox(options=FAST)

    assert service.calls == 2, "первый вход и повторный — в соседнем контексте"
    assert here.cookie_writes == [(sid("s2"),)]
    assert (mail_here.applied, mail_here.applied_by_login_here) == (SessionRevision(2), False)


@pytest.mark.parametrize(("retries", "calls"), [(1, 2), (0, 1)])
async def test_retry_is_spent_once_and_then_the_failure_surfaces(retries: int, calls: int) -> None:
    service = ForgedLogin()
    portal = Mail(
        client(tab(FakeContext(), MailServer()), session_of(service), auth_retries=retries)
    )

    with pytest.raises(SessionExpiredError):
        await portal.open_inbox(options=FAST)

    assert service.calls == calls


@dataclass
class PoolSource:
    """Источник сессии пула: не входит и не повторяет — контекст закроет пул."""

    renewed: list[object] = field(default_factory=list)

    def is_expired(self, error: BaseException) -> bool:
        return isinstance(error, SessionExpiredError)

    async def ensure(self, client: AsyncBrowserClient) -> str:
        _ = client
        return "generation-1"

    async def renew(self, client: AsyncBrowserClient, rejected: str) -> str | None:
        _ = client
        self.renewed.append(rejected)
        return None


async def test_renew_returning_none_re_raises_the_original_failure() -> None:
    source = PoolSource()
    driver = FakeDriver(redirects={INBOX_URL: LOGIN_URL}, pages={INBOX_URL: {"div.inbox"}})

    with pytest.raises(SessionExpiredError):
        await Mail(client(driver, source)).open_inbox(options=FAST)

    assert source.renewed == ["generation-1"]


# --- несколько контекстов -------------------------------------------------------------------


async def test_two_contexts_of_one_account_share_the_store_but_not_the_lock() -> None:
    server, service, store = MailServer(), EntranceLogin(), MemorySessionStore[BrowserState]()
    first, second = FakeContext(), FakeContext()

    await Mail(client(tab(first, server), session_of(service, store))).open_inbox(options=FAST)
    await Mail(client(tab(second, server), session_of(service, store))).open_inbox(options=FAST)

    assert service.calls == 1
    assert (first.cookie_writes, second.cookie_writes) == ([], [(sid("s1"),)])


async def test_session_object_rejects_a_client_from_another_context() -> None:
    server, mail = MailServer(), session_of(EntranceLogin())
    await Mail(client(tab(FakeContext(), server), mail)).open_inbox(options=FAST)

    with pytest.raises(BrowserSessionError, match="one browser context"):
        await Mail(client(tab(FakeContext(), server), mail)).open_inbox(options=FAST)


async def test_login_needs_a_driver_that_accepts_session_state() -> None:
    """Клиент с `session=` драйвер не проверяет; сессия отсекает его до вызова сервиса."""
    service = EntranceLogin()
    portal = Mail(client(FakeDriver(), session_of(service)))

    with pytest.raises(CapabilityMismatchError):
        await portal.open_inbox(options=FAST)

    assert service.calls == 0


async def test_state_resolves_the_session_without_an_operation() -> None:
    service = EntranceLogin()
    mail = session_of(service)

    state = await mail.state(client(tab(FakeContext(), MailServer()), mail))

    assert state == BrowserState(cookies=(sid("s1"),))
    assert service.calls == 1
