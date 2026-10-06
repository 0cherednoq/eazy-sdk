"""Адаптер playwright: страница Playwright в терминах `Driver`.

Здесь и только здесь библиотека знает про playwright. Операции его не видят: им
достаются `Element` и `Driver`, поэтому тот же сценарий пойдёт на любом другом
адаптере, объявившем нужные возможности.

Сеть собирается в буфер подпиской на `response`: тело читается сразу, потому что
после ухода страницы его уже не достать, — но только то, что разрешает политика
захвата (`CapturePolicy`: типы ресурсов, лимит на тело и на весь буфер в байтах). Поэтому `wait_response` отвечает и на
ответ, пришедший **до** вызова, — иначе гонка между действием и ожиданием решалась
бы случайно. Но только на ответ после водораздела `since`: прошлый ответ по тому же
адресу — не ответ на это действие.

Ненайденный узел — обычный исход поиска. Всё остальное, что бросает playwright, —
закрытая страница, оборванное соединение — отказ транспорта, и наружу он уходит
`TransportError` ядра, а не притворяется «не нашлось».

Переходы главного фрейма считаются подпиской на `framenavigated` с самого создания
драйвера: счёт — водораздел для признака `navigated()`, и перезагрузка того же адреса в
нём видна, хотя адрес не изменился.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Self

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from eazy_sdk.handlers import CapabilityLevel, TransportError
from eazy_sdk_browser.fetch import PageFetchError, PageReply, PageRequest
from eazy_sdk_browser.handlers._page_fetch import FETCH_SCRIPT, parse_reply, request_spec
from eazy_sdk_browser.handlers._response_buffer import ResponseBuffer
from eazy_sdk_browser.handlers.capture import DEFAULT_CAPTURE, CapturePolicy
from eazy_sdk_browser.locators import Locator, Pick
from eazy_sdk_browser.network import ResponseView
from eazy_sdk_browser.profile import BrowserProfile
from eazy_sdk_browser.state import BrowserCookie, BrowserState, Origin

if TYPE_CHECKING:
    from collections.abc import Mapping

    # Типы `add_cookies` и `storage_state` наружу не вынесены, хотя без них куку не собрать,
    # не сваливаясь в `dict[str, Any]`. Импорт только для типизатора и только здесь:
    # адаптер — единственное место, которому позволено знать playwright изнутри.
    from playwright._impl._api_structures import (
        OriginState,
        SetCookieParam,
        StorageState,
        StorageStateCookie,
    )
    from playwright.async_api import Frame, FrameLocator, Page, Response
    from playwright.async_api import Locator as PlaywrightLocator

    from eazy_sdk_browser.driver import Element, LoadState

PLAYWRIGHT_PROFILE = BrowserProfile(
    "playwright",
    network=CapabilityLevel.CAPTURE_VERIFIED,
    session_state=CapabilityLevel.CAPTURE_VERIFIED,
    page_requests=CapabilityLevel.CAPTURE_VERIFIED,
    navigation_events=CapabilityLevel.CAPTURE_VERIFIED,
    shadow_dom=CapabilityLevel.CAPTURE_VERIFIED,
    rich_text=CapabilityLevel.CAPTURE_VERIFIED,
)

_SAME_SITE: Mapping[str, Literal["Lax", "None", "Strict"]] = {
    "Lax": "Lax",
    "None": "None",
    "Strict": "Strict",
}
"""Значения `SameSite`, которые принимает playwright. Прочее он отвергает целиком."""


class PlaywrightElement:
    """Узел страницы. Живёт ровно столько, сколько длится действие над ним."""

    def __init__(self, locator: PlaywrightLocator) -> None:
        self._locator = locator

    async def fill(self, value: str) -> None:
        await self._locator.fill(value)

    async def click(self) -> None:
        await self._locator.click()

    async def hover(self) -> None:
        await self._locator.hover()

    async def check(self) -> None:
        await self._locator.check()

    async def uncheck(self) -> None:
        await self._locator.uncheck()

    async def select(self, option: str) -> None:
        """Строка совпадает и со значением, и с подписью пункта — как у `select_option`."""
        await self._locator.select_option(option)

    async def set_html(self, html: str) -> None:
        """Положить разметку в редактор.

        Обычный ввод вставил бы её как текст, а событие `input` обязательно: редактор
        узнаёт об изменении только по нему. Не часть протокола `Element`: это возможность
        драйвера `rich_text`, и снаружи она вызывается через `PlaywrightDriver.set_html`.
        """
        await self._locator.evaluate(
            "(element, html) => { element.innerHTML = html;"
            " element.dispatchEvent(new Event('input', { bubbles: true }));"
            " element.dispatchEvent(new Event('change', { bubbles: true })); }",
            html,
        )

    async def press(self, key: str) -> None:
        await self._locator.press(key)

    async def text(self) -> str:
        return (await self._locator.inner_text()).strip()

    async def value(self) -> str:
        return await self._locator.input_value()

    async def attribute(self, name: str) -> str | None:
        return await self._locator.get_attribute(name)

    async def visible(self) -> bool:
        return await self._locator.is_visible()


class PlaywrightDriver:
    """Страница Playwright в терминах библиотеки.

    Контракт времени жизни: **один драйвер на страницу, и живёт он столько же, сколько
    страница.** Драйвер подписан на события страницы; когда страница закрывается, он
    отписывается сам и новых тел не читает, а `aclose()` (или выход из `async with`)
    дожидается уже начатых. Обернуть ту же страницу второй раз, не закрыв прежний
    драйвер, — ошибка вызывающего: оба читали бы каждое тело.

        async with PlaywrightDriver(page) as driver:
            portal = CompaniesPortal(AsyncBrowserClient(driver))

    Сколько страниц, чьих и когда их открывать, решает слой выше.
    """

    def __init__(self, page: Page, *, capture: CapturePolicy | None = DEFAULT_CAPTURE) -> None:
        self._page = page
        self._response_buffer = ResponseBuffer()
        self._pending: set[asyncio.Task[None]] = set()
        self._capture = capture
        if capture is not None:
            page.on("response", self._remember)
        # Переходы считаются всегда: событие дешёвое, а без него переход не отличить от
        # старой страницы, которая ещё видна сразу после клика.
        self._navigations = 0
        self._attached = True
        page.on("framenavigated", self._navigated)
        page.on("close", self._page_closed)

    @property
    def profile(self) -> BrowserProfile:
        """Без подписки на сеть драйвер её и не обещает — но только её."""
        if self._capture is not None:
            return PLAYWRIGHT_PROFILE
        return replace(PLAYWRIGHT_PROFILE, network=CapabilityLevel.UNSUPPORTED)

    @property
    def page(self) -> Page:
        """Настоящая страница — для того, чего библиотека ещё не умеет."""
        return self._page

    async def find(self, locator: Locator) -> PlaywrightElement | None:
        """Дождаться любого кандидата, потом выбрать по порядку предпочтения.

        Ожидание одно на всех (`or_`), иначе каждый отсутствующий кандидат стоил бы
        полного таймаута. Но `or_` отдаёт узлы в порядке DOM, а не объявления —
        поэтому после ожидания предпочтение восстанавливается среди видимых.
        """
        candidates = self._candidates(locator)
        union = candidates[0]
        for candidate in candidates[1:]:
            union = union.or_(candidate)
        try:
            await union.first.wait_for(state="visible", timeout=locator.patience * 1000)
        except PlaywrightTimeoutError:
            return None
        except Exception as error:
            raise TransportError("playwright", "find", 1, error) from error
        return await self._first_visible(candidates, locator.pick, phase="find")

    async def peek(self, locator: Locator) -> PlaywrightElement | None:
        """Есть ли кандидат прямо сейчас. Ничего не ждёт: ждёт цикл, а не признак."""
        return await self._first_visible(self._candidates(locator), locator.pick, phase="peek")

    async def peek_all(self, locator: Locator) -> list[Element]:
        """Видимые узлы первого кандидата, у которого они есть. Ничего не ждёт.

        Видимость отбирает браузер (`filter(visible=True)`), а не мы после `all()`: и
        счёт, и номер `nth` тогда считаются среди видимых, и скрытая строка-шаблон не
        сдвигает номера строк.
        """
        for candidate in self._candidates(locator):
            shown = candidate.filter(visible=True)
            try:
                count = await shown.count()
            except Exception as error:
                raise TransportError("playwright", "peek_all", 1, error) from error
            if count:
                return [PlaywrightElement(shown.nth(index)) for index in range(count)]
        return []

    async def set_html(self, locator: Locator, html: str) -> bool:
        """Дождаться редактора, как `find`, и положить в него разметку. `False` — узла нет."""
        found = await self.find(locator)
        if found is None:
            return False
        await found.set_html(html)
        return True

    def _candidates(self, locator: Locator) -> list[PlaywrightLocator]:
        scope = self._within(locator.frames)
        return [scope.locator(selector) for selector in locator.selectors]

    async def _first_visible(
        self, candidates: list[PlaywrightLocator], pick: Pick, *, phase: str
    ) -> PlaywrightElement | None:
        for candidate in candidates:
            chosen = candidate.last if pick is Pick.last else candidate.first
            try:
                visible = await chosen.is_visible()
            except Exception as error:
                raise TransportError("playwright", phase, 1, error) from error
            if visible:
                return PlaywrightElement(chosen)
        return None

    def _within(self, frames: tuple[str, ...]) -> Page | FrameLocator:
        """Спуститься по цепочке фреймов от страницы вглубь."""
        scope: Page | FrameLocator = self._page
        for frame in frames:
            scope = scope.frame_locator(frame)
        return scope

    async def location(self) -> str:
        return self._page.url

    async def page_text(self) -> str:
        """Видимый текст страницы, а не разметка: признаки отказа пишут по-русски."""
        return await self._page.inner_text("body")

    async def goto(self, url: str, *, wait: LoadState = "load", within: float) -> None:
        """Открыть адрес. Не открылся — сеть, срок, закрытая страница — отказ транспорта.

        Истёкший срок здесь тоже отказ: у ненайденного элемента есть обычный исход, а у
        страницы, не дождавшейся своего события, смотреть операции не на что.
        """
        try:
            await self._page.goto(url, wait_until=wait, timeout=within * 1000)
        except Exception as error:
            raise TransportError("playwright", "goto", 1, error) from error

    def navigations(self) -> int:
        """Сколько раз главный фрейм переходил — по событиям, а не опросом адреса."""
        return self._navigations

    def mark(self) -> int:
        """Позиция буфера сейчас: ответы, которые придут после, — новые."""
        return self._response_buffer.mark()

    async def wait_response(
        self, url_contains: str, *, within: float, since: int = 0
    ) -> ResponseView | None:
        """Ответ по адресу: уже пришедший или тот, что придёт за отведённое время."""
        return await self._response_buffer.wait(url_contains, within=within, since=since)

    async def export_state(self) -> BrowserState:
        """Куки и localStorage контекста целиком — то, что делает вход «уже бывшим»."""
        return from_storage_state(await self._page.context.storage_state())

    async def add_cookies(self, cookies: tuple[BrowserCookie, ...]) -> None:
        """Положить куки в контекст страницы — сразу и один раз.

        localStorage сюда не входит: в живой контекст его можно положить только
        стартовым скриптом, а тот срабатывает на каждой навигации и затирает то, что
        сайт успел обновить. localStorage задаётся при создании контекста —
        `to_storage_state`.
        """
        if cookies:
            await self._page.context.add_cookies([_cookie_out(cookie) for cookie in cookies])

    def context_key(self) -> object:
        """Контекст страницы: у всех его вкладок он один и тот же объект."""
        return self._page.context

    async def fetch(self, request: PageRequest, *, frames: tuple[str, ...] = ()) -> PageReply:
        """Выполнить запрос от имени документа — верхнего или указанного фрейма.

        Тело ходит туда и обратно в base64: `evaluate` обменивается только тем, что
        переживает JSON, а тела бывают двоичными.
        """
        scope = await self._frame(frames)
        reply: dict[str, Any] = await scope.evaluate(FETCH_SCRIPT, request_spec(request))
        return parse_reply(request, reply)

    async def _frame(self, frames: tuple[str, ...]) -> Page | Frame:
        """Документ, от имени которого пойдёт запрос.

        Не `frame_locator`: тот умеет искать узлы, а нам нужен сам документ — у него
        свой источник, и именно источник решает, пустит ли сервис этот запрос.
        """
        scope: Page | Frame = self._page
        for selector in frames:
            handle = await scope.locator(selector).first.element_handle()
            child = await handle.content_frame() if handle is not None else None
            if child is None:
                msg = f"фрейм {selector!r} не найден"
                raise PageFetchError(selector, msg)
            scope = child
        return scope

    async def aclose(self) -> None:
        """Отписаться и дождаться чтения тел: иначе задачи переживут страницу. Идемпотентен."""
        self._detach()
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def _page_closed(self, page: Page) -> None:
        """Страница закрыта: отписаться сразу, синхронно, — новых тел больше не будет."""
        _ = page
        self._detach()

    def _detach(self) -> None:
        if not self._attached:
            return
        self._attached = False
        if self._capture is not None:
            self._page.remove_listener("response", self._remember)
        self._page.remove_listener("framenavigated", self._navigated)
        self._page.remove_listener("close", self._page_closed)

    def _navigated(self, frame: Frame) -> None:
        """Переход фрейма. В счёт идут только переходы главного: фреймы рекламы не в счёт."""
        if frame.parent_frame is None:
            self._navigations += 1

    def _remember(self, response: Response) -> None:
        """Тело читается отдельной задачей: обработчик события синхронный.

        Тип ресурса проверяется здесь, до чтения: картинка, шрифт и бандл в буфер не
        попадают и позиции не занимают.
        """
        capture = self._capture
        if not self._attached or capture is None:
            return
        if response.request.resource_type not in capture.resource_types:
            return
        task = asyncio.create_task(self._store(response))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _store(self, response: Response) -> None:
        capture = DEFAULT_CAPTURE if self._capture is None else self._capture
        declared = _declared_length(response)
        try:
            body = b"" if declared > capture.max_body_bytes else await response.body()
            headers = tuple((await response.all_headers()).items())
        except Exception:  # noqa: BLE001 — тело успело пропасть: ответ просто не попадёт в буфер
            return
        # Длины не было или она соврала: тело уже прочитано, но держать его не будем.
        dropped = max(declared, len(body)) > capture.max_body_bytes
        self._response_buffer.add(
            ResponseView(
                url=response.url,
                status=response.status,
                body=b"" if dropped else body,
                headers=headers,
                body_dropped=dropped,
            ),
            max_total_bytes=capture.max_total_bytes,
        )


def _declared_length(response: Response) -> int:
    """`content-length` ответа; нет его или он не число — ноль, решит длина тела."""
    raw = response.headers.get("content-length", "")
    return int(raw) if raw.isdigit() else 0


def to_storage_state(state: BrowserState) -> StorageState:
    """Состояние в формате `browser.new_context(storage_state=…)`.

    Единственный способ положить localStorage в контекст без стартового скрипта —
    задать его при создании контекста. Создаёт контекст слой выше, поэтому здесь —
    только перевод формата.
    """
    cookies: list[StorageStateCookie] = []
    for cookie in state.cookies:
        raw: StorageStateCookie = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path or "/",
            # -1 — сессионная кука: так её отдаёт и принимает playwright.
            "expires": cookie.expires_at.timestamp() if cookie.expires_at else -1,
            "httpOnly": cookie.http_only,
            "secure": cookie.secure,
        }
        same_site = _SAME_SITE.get(cookie.same_site)
        if same_site is not None:
            raw["sameSite"] = same_site
        cookies.append(raw)
    origins: list[OriginState] = [
        {
            "origin": origin.origin,
            "localStorage": [{"name": name, "value": value} for name, value in origin.items],
        }
        for origin in state.origins
    ]
    return {"cookies": cookies, "origins": origins}


def from_storage_state(raw: Mapping[str, Any]) -> BrowserState:
    """Состояние из формата `context.storage_state()` — обратное `to_storage_state`."""
    return BrowserState(
        cookies=tuple(_cookie_in(item) for item in raw.get("cookies", ())),
        origins=tuple(_origin_in(item) for item in raw.get("origins", ())),
    )


def _cookie_in(raw: Mapping[str, Any]) -> BrowserCookie:
    """Кука playwright в терминах библиотеки. `expires == -1` — сессионная."""
    expires = raw.get("expires", -1)
    return BrowserCookie(
        name=raw.get("name", ""),
        value=raw.get("value", ""),
        domain=raw.get("domain", ""),
        path=raw.get("path", "/"),
        expires_at=datetime.fromtimestamp(expires, tz=UTC) if expires and expires > 0 else None,
        secure=bool(raw.get("secure", False)),
        http_only=bool(raw.get("httpOnly", False)),
        same_site=str(raw.get("sameSite", "") or ""),
    )


def _cookie_out(cookie: BrowserCookie) -> SetCookieParam:
    """Кука в терминах playwright. Без домена браузер её не примет — это отказ."""
    if not cookie.domain:
        msg = f"кука {cookie.name!r} без домена: браузеру некуда её положить"
        raise ValueError(msg)
    raw: SetCookieParam = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "secure": cookie.secure,
        "httpOnly": cookie.http_only,
    }
    if cookie.expires_at is not None:
        raw["expires"] = cookie.expires_at.timestamp()
    same_site = _SAME_SITE.get(cookie.same_site)
    if same_site is not None:
        raw["sameSite"] = same_site
    return raw


def _origin_in(raw: Mapping[str, Any]) -> Origin:
    items = raw.get("localStorage", ())
    return Origin(
        origin=raw.get("origin", ""),
        items=tuple((item.get("name", ""), item.get("value", "")) for item in items),
    )
