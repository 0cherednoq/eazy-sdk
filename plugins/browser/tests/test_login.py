"""Вход через браузер: объявление входа и его чистые методы, клиент без сессии, мост в HTTP.

Объявление одно на сайт: как войти, какая кука обязательна, какие отказы значат «сессия
кончилась». Жизненный цикл сессии — в `test_session.py`. HTTP-клиенту сессия отдаётся
мостом ядра — кукой в `Auth`.
"""

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
    BrowserCookieBridge,
    BrowserDeclarationError,
    BrowserLogin,
    BrowserLoginContext,
    BrowserOperation,
    BrowserSessionError,
    BrowserState,
    Element,
    Failure,
    LoadState,
    PageError,
    browser_cookie_auth,
    css,
    url,
)
from eazy_sdk_browser.testing import StatefulFakeDriver

from eazy_sdk import AsyncApi, AsyncClient, Http, HttpOperation, Identity, op
from eazy_sdk.auth import CookieScheme
from eazy_sdk.testing import AsyncRecordingHandler

pytestmark = pytest.mark.unit

BASE = "https://mail.example"
LOGIN_URL = f"{BASE}/login"
INBOX_URL = f"{BASE}/inbox"
NOW = datetime(2030, 1, 1, tzinfo=UTC)
FAST = BrowserCallOptions(timeout=0.05, element_timeout=0.05)
LOGIN_FORM = {'input[name="user"]', 'input[name="password"]', "button.login"}
MAIL_SESSION = CookieScheme("sid", name="mail-session")


class SessionExpiredError(PageError):
    """Почта увела на вход."""


def sid(value: str, *, lifetime: timedelta = timedelta(days=1)) -> BrowserCookie:
    return BrowserCookie(name="sid", value=value, domain=".mail.example", expires_at=NOW + lifetime)


@dataclass(frozen=True, slots=True)
class Credentials:
    user: str
    password: str = field(repr=False)


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
    """Страница входа: правила «увели на вход» здесь нет — здесь вход и так."""

    open_login = op(OpenLogin)
    sign_in = op(SignIn)


class Mail(AsyncBrowserApi):
    errors = (Failure(when=url.contains("/login"), exception=SessionExpiredError),)

    open_inbox = op(OpenInbox)


@dataclass
class MailDriver(StatefulFakeDriver):
    """Почта: вход ставит новую куку, а в ящик пускают только с той, которую сервер помнит."""

    server_knows: str | None = None
    issued: int = 0

    def on_click(self, selector: str) -> None:
        if selector == "button.login":
            self.issued += 1
            self.server_knows = f"s{self.issued}"
            self.state = BrowserState(cookies=(sid(self.server_knows),))

    async def goto(self, url: str, *, wait: LoadState = "load", within: float) -> None:
        self.redirects = {} if self._known() else {INBOX_URL: LOGIN_URL}
        await super().goto(url, wait=wait, within=within)

    def _known(self) -> bool:
        return self.server_knows is not None and any(
            cookie.name == "sid" and cookie.value == self.server_knows
            for cookie in self.state.cookies
        )


def mail_driver() -> MailDriver:
    return MailDriver(pages={LOGIN_URL: set(LOGIN_FORM), INBOX_URL: {"div.inbox"}})


@dataclass
class EntranceLogin:
    """Сервис входа: операции страницы входа через клиент, который сам не входит."""

    calls: int = 0
    clients: list[AsyncBrowserClient] = field(default_factory=list)

    async def acquire(self, credentials: Credentials, context: BrowserLoginContext) -> BrowserState:
        self.calls += 1
        self.clients.append(context.client)
        entrance = Entrance(context.client)
        await entrance.open_login(options=FAST)
        await entrance.sign_in(user=credentials.user, password=credentials.password, options=FAST)
        return await context.export_state()


@dataclass
class RefreshingLogin(EntranceLogin):
    """Сервис со своим `refresh`: продлевает сессию, не проходя форму заново."""

    refreshed: int = 0

    async def refresh(self, session: BrowserState, context: BrowserLoginContext) -> BrowserState:
        _ = context
        self.refreshed += 1
        return BrowserState(cookies=(sid(f"{session.cookies[0].value}+"),))


MAIL_LOGIN = BrowserLogin(
    service=EntranceLogin(),
    cookies=("sid",),
    expired=(SessionExpiredError,),
    clock=lambda: NOW,
)
"""Одно объявление на сайт: учётных данных и хранилища в нём нет."""


def login_of(service: object) -> BrowserLogin[Credentials]:
    return BrowserLogin(
        service=cast("Any", service),
        cookies=("sid",),
        expired=(SessionExpiredError,),
        clock=lambda: NOW,
    )


def browser(driver: MailDriver) -> AsyncBrowserClient:
    return AsyncBrowserClient(driver, base_url=BASE)


# --- объявление входа: чистые методы ----------------------------------------------------------


async def test_sign_in_runs_the_service_on_the_given_client_and_returns_a_valid_state() -> None:
    service = EntranceLogin()
    driver = mail_driver()
    entrance = browser(driver)

    state = await login_of(service).sign_in(entrance, Credentials("ada", "secret"))

    assert state == BrowserState(cookies=(sid("s1"),))
    assert service.clients == [entrance]
    assert driver.log[-1] == "click button.login"


async def test_sign_in_rejects_a_state_without_the_declared_cookie() -> None:
    """Кука, которая истечёт раньше `leeway`, уже не живая: вход не удался, хоть и завершился."""

    @dataclass
    class ShortLived:
        async def acquire(
            self, credentials: Credentials, context: BrowserLoginContext
        ) -> BrowserState:
            _ = credentials, context
            return BrowserState(cookies=(sid("s1", lifetime=timedelta(seconds=10)),))

    with pytest.raises(BrowserSessionError, match="no live sid"):
        await login_of(ShortLived()).sign_in(browser(mail_driver()), Credentials("ada", "x"))


async def test_refresh_prefers_the_service_refresh_and_falls_back_to_sign_in() -> None:
    old = BrowserState(cookies=(sid("s7"),))
    own, plain = RefreshingLogin(), EntranceLogin()

    refreshed = await login_of(own).refresh(browser(mail_driver()), Credentials("ada", "x"), old)
    signed = await login_of(plain).refresh(browser(mail_driver()), Credentials("ada", "x"), old)

    assert (refreshed, own.refreshed, own.calls) == (BrowserState(cookies=(sid("s7+"),)), 1, 0)
    assert (signed, plain.calls) == (BrowserState(cookies=(sid("s1"),)), 1)


def test_is_expired_recognises_only_declared_failures() -> None:
    assert MAIL_LOGIN.is_expired(SessionExpiredError("увела на вход"))
    assert not MAIL_LOGIN.is_expired(PageError("другой отказ"))
    assert not MAIL_LOGIN.is_expired(RuntimeError("совсем другой"))


async def test_one_login_declaration_serves_two_accounts() -> None:
    """Одно объявление, два набора учётных данных — два разных входа, два состояния."""
    ada, bob = mail_driver(), mail_driver()
    ada.issued = 10

    first = await MAIL_LOGIN.sign_in(browser(ada), Credentials("ada", "a"))
    second = await MAIL_LOGIN.sign_in(browser(bob), Credentials("bob", "b"))

    assert (first, second) == (
        BrowserState(cookies=(sid("s11"),)),
        BrowserState(cookies=(sid("s1"),)),
    )
    assert 'fill input[name="user"] = ada' in ada.log
    assert 'fill input[name="user"] = bob' in bob.log


async def test_client_without_session_never_signs_in() -> None:
    """`session=None`: операция идёт как есть, объявленный отказ не повторяется."""
    driver = mail_driver()

    with pytest.raises(SessionExpiredError):
        await Mail(browser(driver)).open_inbox(options=FAST)

    assert driver.url == LOGIN_URL
    assert not any(line.startswith("fill") for line in driver.log)


def test_login_service_must_acquire_asynchronously() -> None:
    class Blocking:
        def acquire(self, credentials: Credentials, context: BrowserLoginContext) -> BrowserState:
            _ = credentials, context
            return BrowserState()

    with pytest.raises(BrowserDeclarationError, match="async acquire"):
        login_of(Blocking())


def test_auth_retries_cannot_be_negative() -> None:
    with pytest.raises(BrowserDeclarationError, match="auth_retries"):
        BrowserClientConfig(auth_retries=-1)


# --- B5.3: мост в HTTP-`Auth` ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Folders(HttpOperation[bytes]):
    __http__ = Http.get("/api/folders", security=MAIL_SESSION)


class MailApi(AsyncApi):
    folders = op(Folders)


def test_bridge_picks_the_cookie_by_name_and_then_by_domain() -> None:
    state = BrowserState(
        cookies=(BrowserCookie(name="sid", value="sso", domain=".id.example"), sid("mail"))
    )

    anywhere = BrowserCookieBridge("sid").convert(state)
    on_mail = BrowserCookieBridge("sid", domain="mail.example").convert(state)

    assert anywhere.value == "sso"
    assert (on_mail.value, on_mail.domain, on_mail.expires_at) == (
        "mail",
        ".mail.example",
        NOW + timedelta(days=1),
    )
    with pytest.raises(BrowserSessionError, match="'token'"):
        BrowserCookieBridge("token").convert(state)


async def test_http_router_sends_the_cookie_of_the_browser_session() -> None:
    """Вход — браузером, запрос — HTTP-клиентом с той же кукой."""
    mail = login_of(EntranceLogin()).session(Credentials("ada", "secret"))
    client = AsyncBrowserClient(mail_driver(), base_url=BASE, session=mail)
    handler = AsyncRecordingHandler(status=200, content=b"[]")

    auth = await browser_cookie_auth(
        await mail.state(client), MAIL_SESSION, "sid", clock=lambda: NOW
    )
    async with AsyncClient(base_url=BASE, handler=handler) as http:
        await MailApi(http, identity=Identity(auth=(auth,))).folders()

    assert handler.last_request.headers.get("cookie") == "sid=s1"


async def test_expired_browser_cookie_is_not_handed_to_http() -> None:
    state = BrowserState(cookies=(sid("s1", lifetime=-timedelta(minutes=1)),))

    with pytest.raises(BrowserSessionError, match="expired"):
        await browser_cookie_auth(state, MAIL_SESSION, "sid", clock=lambda: NOW)
