"""Адаптер: браузер в роли транспорта `eazy-sdk`.

Обратная сторона `integrations.eazy_sdk`. Там браузер **перехватывал** ответ, а
объявление его разбирало; здесь то же объявление само **делает** запрос — руками
страницы:

    handler = BrowserHandler(driver, frames=Frames.MAIL)
    async with AsyncClient(base_url="https://maillist.gmx.com", handler=handler) as client:
        page = await MailApi(client).mail_list(offset=50)

Выигрыш не в красоте, а в том, что снимается два ограничения сразу. Со стороны
браузера — больше не нужно нажимать «дальше», чтобы получить вторую страницу: любой
`offset` запрашивается прямо. Со стороны HTTP — не нужно объяснять сервису, кто мы:
вход уже пройден страницей, и запрос уходит её куками, её TLS и её источником.

Ценой — то, чего браузер не даёт, и профиль говорит это прямо, а не выясняется в
рантайме:

* **порядок, регистр и повторы заголовков** — `fetch` складывает их в `Headers`,
  который приводит имена к нижнему регистру, сортирует и склеивает одноимённые;
* **ручной `Cookie`** запрещён спецификацией: куки ставит браузер. Попытка задать его
  не игнорируется молча, а отвергается — иначе запрос ушёл бы не с теми куками, что
  просил вызывающий;
* **редиректы** не остановить: при `redirect: 'manual'` ответ приходит непрозрачным,
  со статусом `0` и без `Location`, — поэтому объявлено `UNCONTROLLED`;
* **заголовки ответа** с чужого источника видны не все: только разрешённые CORS.
  `content-type` в их числе, так что выбор случая по типу содержимого работает.

Зависимость необязательная: ядро знает только протокол `FetchAware`, а сам `eazy-sdk`
ставится экстрой `eazy-browser[eazy-sdk]`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from typing import TYPE_CHECKING

from zapros import AsyncBaseHandler, Response

from eazy_sdk.handlers.profile import (
    AutomaticHeaderPolicy,
    CapabilityLevel,
    HandlerProfile,
    RedirectControl,
)
from eazy_sdk.request.prepared import HttpProtocol
from eazy_sdk_browser.fetch import FETCH_TIMEOUT, Credentials, PageRequest, require_fetch

if TYPE_CHECKING:
    from zapros import Request

    from eazy_sdk_browser.driver import Driver

BROWSER_HANDLER_PROFILE = HandlerProfile(
    # Версию протокола выбирает браузер, договариваясь с сервисом, и назад её не
    # сообщает. Умеет он все три — это и объявлено; какой ушёл на самом деле,
    # обработчик не знает и не обещает.
    protocols=frozenset({HttpProtocol.HTTP_1_1, HttpProtocol.HTTP_2, HttpProtocol.HTTP_3}),
    exact_target=CapabilityLevel.BEST_EFFORT,
    header_order=CapabilityLevel.UNSUPPORTED,
    header_casing=CapabilityLevel.UNSUPPORTED,
    duplicate_headers=CapabilityLevel.UNSUPPORTED,
    preencoded_body=CapabilityLevel.BEST_EFFORT,
    manual_cookie_field=CapabilityLevel.UNSUPPORTED,
    automatic_headers=AutomaticHeaderPolicy.TRANSPORT_CONTROLLED,
    redirects=RedirectControl.UNCONTROLLED,
    replayable_streams=CapabilityLevel.UNSUPPORTED,
)

FORBIDDEN_HEADERS = frozenset(
    {
        "accept-charset",
        "accept-encoding",
        "access-control-request-headers",
        "access-control-request-method",
        "connection",
        "content-length",
        "cookie",
        "date",
        "dnt",
        "expect",
        "host",
        "keep-alive",
        "origin",
        "referer",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
        "via",
    }
)
"""Имена, которыми распоряжается сам браузер.

Почти все он просто отбросит, но `user-agent` — нет: `fetch` даёт его переопределить.
И это опаснее молчаливого отказа. `zapros` подставляет свой `python-zapros/0.16.0`
каждому запросу, и он **дошёл бы до сервиса** — то есть запрос, взятый у браузера ради
его неотличимости, представился бы Python-клиентом. Личность транспорта здесь —
браузер целиком, и спорить с ней заголовком нельзя.
"""

_DECODED_BY_BROWSER = frozenset({"content-encoding", "content-length"})
"""`fetch` отдаёт тело уже распакованным, а эти заголовки описывают упакованное."""


class BrowserHandler(AsyncBaseHandler):
    """Обработчик `zapros`, выполняющий запрос изнутри страницы.

    `frames` задаёт документ-источник: у приложения во фрейме свой `Origin`, и сервис
    отвечает на его запросы иначе, чем на запросы верхней страницы.

    `credentials` — как у самого браузера: своему источнику куки уходят, чужому нет.
    `include` шлёт их и чужому, но требует разрешения сервиса, и там, где сервис
    авторизует по токену, превращает рабочий запрос в запрет CORS.
    """

    profile = BROWSER_HANDLER_PROFILE

    def __init__(
        self,
        driver: Driver,
        *,
        frames: tuple[str, ...] = (),
        credentials: Credentials = "same-origin",
        timeout: float = FETCH_TIMEOUT,
    ) -> None:
        self._driver = require_fetch(driver)
        self._frames = frames
        self._credentials: Credentials = credentials
        self._timeout = timeout
        self.profile = replace(BROWSER_HANDLER_PROFILE, impersonation=driver.profile.name)

    async def ahandle(self, request: Request) -> Response:
        """Выполнить подготовленный запрос браузером и вернуть ответ `zapros`."""
        reply = await self._driver.fetch(
            PageRequest(
                method=request.method,
                url=str(request.url),
                headers=_headers_of(request),
                body=await _body_of(request),
                credentials=self._credentials,
                timeout=_timeout_of(request, self._timeout),
            ),
            frames=self._frames,
        )
        return Response(
            reply.status,
            [(name, value) for name, value in reply.headers if name not in _DECODED_BY_BROWSER],
            content=reply.body,
            request=request,
        )

    async def aclose(self) -> None:
        """Драйвер открывал не мы — закрывать его не нам."""


def _headers_of(request: Request) -> tuple[tuple[str, str], ...]:
    """Заголовки, которые браузер действительно отправит.

    `Cookie` не отбрасывается молча: вызывающий просил определённые куки, а ушли бы
    куки браузера — тихая подмена там, где решается, от чьего имени идёт запрос.
    """
    out: list[tuple[str, str]] = []
    for name in request.headers:
        lowered = name.lower()
        if lowered == "cookie":
            msg = "браузер ставит куки сам: запрос с ручным Cookie ушёл бы не с теми"
            raise ValueError(msg)
        if lowered in FORBIDDEN_HEADERS or lowered.startswith(("sec-", "proxy-")):
            continue
        out.extend((name, value) for value in request.headers.getall(name))
    return tuple(out)


async def _body_of(request: Request) -> bytes | None:
    """Тело целиком: `fetch` не умеет отдавать его потоком, и профиль это объявляет."""
    body = request.body
    if body is None:
        return None
    if isinstance(body, bytes):
        return body
    if isinstance(body, AsyncIterator):
        return b"".join([chunk async for chunk in body])
    if isinstance(body, Iterator):
        return b"".join(body)
    msg = f"тело запроса неподдерживаемого вида: {type(body).__name__}"
    raise TypeError(msg)


def _timeout_of(request: Request, default: float) -> float:
    timeouts = request.context.get("timeouts")
    total = timeouts.get("total") if timeouts is not None else None
    return default if total is None else float(total)


__all__ = ["BROWSER_HANDLER_PROFILE", "FORBIDDEN_HEADERS", "BrowserHandler"]
