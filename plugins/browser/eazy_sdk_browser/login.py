"""Вход через браузер: знание сайта — как войти и как понять, что сессия кончилась.

Объявление входа одно на сайт и общее для всех аккаунтов: в нём нет ни учётных данных, ни
хранилища, ни повторов. Оно говорит, **как** войти (`service`), без какой куки сессия не
сессия (`cookies`) и какие отказы значат «сессия кончилась» (`expired`):

    class MailLogin:
        async def acquire(
            self, credentials: MailCredentials, context: BrowserLoginContext
        ) -> BrowserState:
            entrance = Entrance(context.client)
            await entrance.open_login()
            await entrance.sign_in(user=credentials.user, password=credentials.password)
            return await context.export_state()

    MAIL_LOGIN = BrowserLogin(
        service=MailLogin(),
        cookies=("sid",),                     # без живой `sid` сессия не сессия
        expired=(SessionExpiredError,),       # отказ, после которого входят заново
    )

Чистые методы — `validate`, `is_expired`, `sign_in`, `refresh` — не держат состояния:
ими пользуется и `BrowserSession` (жизненный цикл сессии одного контекста, его строит
`MAIL_LOGIN.session(credentials, store=...)`), и слой выше, у которого свой жизненный
цикл (пул контекстов).

HTTP-клиенту та же сессия отдаётся мостом ядра: `BridgedSessionAdopter` с
`BrowserCookieBridge` выбирает куку, `CookieAuthAdopter` строит из неё `Auth` —
`browser_cookie_auth(state, scheme, "sid")` делает оба шага.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from eazy_sdk.auth import Auth
from eazy_sdk.auth.cookies import HttpCookieSession
from eazy_sdk.auth.session import BridgedSessionAdopter
from eazy_sdk_browser.errors import BrowserDeclarationError, BrowserError
from eazy_sdk_browser.state import BrowserCookie, BrowserState, require_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from eazy_sdk.auth import AuthScheme
    from eazy_sdk.auth.session import SessionStore
    from eazy_sdk_browser.client import AsyncBrowserClient
    from eazy_sdk_browser.session import BrowserSession


def _now() -> datetime:
    return datetime.now(UTC)


class BrowserSessionError(BrowserError):
    """Сессия браузера не годится туда, куда её отдают: нужной куки нет или она истекла."""


@dataclass(frozen=True, slots=True)
class BrowserLoginContext:
    """Что получает сервис входа: клиент на той же странице, но без сессии.

    Клиент — без сессии намеренно: операции входа, запущенные через клиент с сессией, сами
    потребовали бы входа, и тот зациклился бы.
    """

    client: AsyncBrowserClient

    async def export_state(self) -> BrowserState:
        """Состояние браузера после входа — то, что станет сессией."""
        return await require_state(self.client.driver).export_state()


class BrowserLoginService[TCredentials](Protocol):
    """Сервис входа: `acquire` обязателен, `refresh` — нет, без него входят заново."""

    async def acquire(
        self, credentials: TCredentials, context: BrowserLoginContext
    ) -> BrowserState: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class BrowserLogin[TCredentials]:
    """Объявление входа на сайт. Одно на сайт, общее для всех аккаунтов.

    Учётные данные и хранилище сюда не входят: это данные аккаунта, а не знание сайта.
    Их получает `session(...)` — жизненный цикл сессии одного контекста — или вызывающий
    передаёт их в `sign_in`/`refresh` сам.
    """

    service: BrowserLoginService[TCredentials]
    cookies: tuple[str, ...] = ()
    """Куки, без живого экземпляра которых сессия не сессия. Пусто — годится любое непустое
    состояние (для `session(...)` пусто нельзя)."""
    expired: tuple[type[Exception], ...] = ()
    """Отказы операций, после которых входят заново и повторяют операцию."""
    leeway: timedelta = timedelta(seconds=30)
    """Кука, которая истечёт раньше, уже не считается живой."""
    clock: Callable[[], datetime] = field(default=_now, repr=False)

    def __post_init__(self) -> None:
        acquire = getattr(self.service, "acquire", None)
        if not inspect.iscoroutinefunction(acquire):
            msg = (
                f"login service {type(self.service).__qualname__} must define "
                "async acquire(credentials, context)"
            )
            raise BrowserDeclarationError(msg)

    def validate(self, value: BrowserState) -> bool:
        """Годится ли сессия: состояние не пустое, нужные куки живы с запасом `leeway`."""
        if value.is_empty():
            return False
        horizon = self.clock() + self.leeway
        return all(_alive(value.cookies, name, horizon) for name in self.cookies)

    def is_expired(self, error: BaseException) -> bool:
        """Этот отказ значит «сессия кончилась»? Только объявленные в `expired`."""
        return isinstance(error, self.expired)

    async def sign_in(self, client: AsyncBrowserClient, credentials: TCredentials) -> BrowserState:
        """Войти на странице клиента. Клиент должен быть без сессии — иначе вход зациклится.

        Результат проверяется: вход завершился, а сессия не годится — `BrowserSessionError`.
        """
        state = await self.service.acquire(credentials, BrowserLoginContext(client))
        return self._checked(state, "sign-in")

    async def refresh(
        self, client: AsyncBrowserClient, credentials: TCredentials, state: BrowserState
    ) -> BrowserState:
        """Обновить сессию: свой `refresh` сервиса, если он есть, иначе — войти заново."""
        own = getattr(self.service, "refresh", None)
        if not callable(own):
            return await self.sign_in(client, credentials)
        refreshed: BrowserState = await own(state, BrowserLoginContext(client))
        return self._checked(refreshed, "refresh")

    def session(
        self,
        credentials: TCredentials,
        *,
        store: SessionStore[BrowserState] | None = None,
        identity: str = "client",
    ) -> BrowserSession[TCredentials]:
        """Жизненный цикл сессии **одного контекста**: один объект на все его вкладки.

        `store` — общий для всех контекстов аккаунта: только он согласует их ревизии. `None`
        — хранилище в памяти этого объекта, годится для одного контекста. `identity` — кто
        входит, ключ сессии в хранилище (`browser:<identity>`); не секрет.
        """
        if not self.cookies:
            msg = (
                "BrowserLogin.session() needs declared cookies=(...): without them any "
                "non-empty context (an analytics cookie) passes as a live session"
            )
            raise BrowserDeclarationError(msg)
        # Лениво: `session` импортирует этот модуль на уровне модуля.
        from eazy_sdk_browser.session import BrowserSession

        return BrowserSession(self, credentials, store=store, identity=identity)

    def _checked(self, state: BrowserState, step: str) -> BrowserState:
        if not self.validate(state):
            names = ", ".join(self.cookies) or "any cookie"
            msg = f"{step} finished, but the session is not valid: no live {names}"
            raise BrowserSessionError(msg)
        return state


def _alive(cookies: tuple[BrowserCookie, ...], name: str, horizon: datetime) -> bool:
    return any(
        cookie.name == name and (cookie.expires_at is None or cookie.expires_at > horizon)
        for cookie in cookies
    )


# --- мост в HTTP -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrowserCookieBridge:
    """`SessionBridge[BrowserState, HttpCookieSession]`: кука входа из браузера — HTTP-клиенту.

    Выбор по имени, а если одноимённых несколько (кука сервиса и кука его SSO) — по домену.
    Атрибуты переносятся все: HTTP-стороне важен срок, чтобы не слать мёртвую куку.
    """

    cookie_name: str
    domain: str | None = None

    def convert(self, value: BrowserState) -> HttpCookieSession:
        for cookie in value.cookies:
            if cookie.name == self.cookie_name and self._on_domain(cookie):
                return HttpCookieSession(
                    cookie.value,
                    domain=cookie.domain,
                    path=cookie.path,
                    secure=cookie.secure,
                    http_only=cookie.http_only,
                    same_site=cookie.same_site,
                    expires_at=cookie.expires_at,
                )
        where = "" if self.domain is None else f" on {self.domain}"
        msg = f"browser session has no cookie {self.cookie_name!r}{where}"
        raise BrowserSessionError(msg)

    def _on_domain(self, cookie: BrowserCookie) -> bool:
        return self.domain is None or cookie.domain.lstrip(".") == self.domain.lstrip(".")


@dataclass(frozen=True, slots=True)
class CookieAuthAdopter:
    """`SessionAdopter[HttpCookieSession]`: кука — в `Auth` схемы HTTP-операций.

    `Auth` получается статическим: у ядра нет cookie-привязки, которая приняла бы сессию со
    стороны без собственного HTTP-входа. Поэтому после повторного входа в браузере мост
    проходят заново. Истёкшую куку адаптер не отдаёт вовсе.
    """

    scheme: AuthScheme[str]
    clock: Callable[[], datetime] = field(default=_now, repr=False)

    async def adopt(self, value: HttpCookieSession) -> Auth:
        if not value.is_active(self.clock()):
            msg = "browser cookie has expired; sign in again before handing it to HTTP"
            raise BrowserSessionError(msg)
        return self.scheme.static(value.value)


async def browser_cookie_auth(
    state: BrowserState,
    scheme: AuthScheme[str],
    cookie_name: str,
    *,
    domain: str | None = None,
    clock: Callable[[], datetime] = _now,
) -> Auth:
    """Браузерная сессия — в `Auth` HTTP-клиента мостом ядра (`BridgedSessionAdopter`).

    auth = await browser_cookie_auth(await mail.state(client), MAIL_SESSION, "sid")
    folders = await MailApi(http, identity=Identity(auth=(auth,))).folders()
    """
    bridge = BridgedSessionAdopter(
        BrowserCookieBridge(cookie_name, domain), CookieAuthAdopter(scheme, clock)
    )
    adopted = await bridge.adopt(state)
    if not isinstance(adopted, Auth):
        msg = f"cookie adopter returned {type(adopted).__name__}, not Auth"
        raise TypeError(msg)
    return adopted


__all__ = [
    "BrowserCookieBridge",
    "BrowserLogin",
    "BrowserLoginContext",
    "BrowserLoginService",
    "BrowserSessionError",
    "CookieAuthAdopter",
    "browser_cookie_auth",
]
