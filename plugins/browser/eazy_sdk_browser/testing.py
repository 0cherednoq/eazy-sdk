"""Драйверы для тестов: страница без браузера.

Публикуются наравне с остальной библиотекой, как `eazy_sdk.testing` публикует свои
записывающие обработчики. Автору SDK они нужны, чтобы проверять сценарий без Chromium,
а автору адаптера — чтобы убедиться, что его профиль не расходится с тем, что адаптер
на самом деле умеет: «лгущие» драйверы здесь ровно для этого.

Соответствие протоколу проверяется отдельным тестом — иначе рост `Driver` тихо
расходился бы с фейком, и часть тестов проверяла бы несуществующий контракт.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from eazy_sdk.handlers import CapabilityLevel
from eazy_sdk_browser.driver import Element
from eazy_sdk_browser.fetch import PageFetchError, PageReply, PageRequest
from eazy_sdk_browser.locators import Locator
from eazy_sdk_browser.network import ResponseView
from eazy_sdk_browser.profile import BrowserProfile
from eazy_sdk_browser.state import BrowserCookie, BrowserState

if TYPE_CHECKING:
    from eazy_sdk_browser.driver import LoadState

FRAME_SEPARATOR = " >>> "
"""Как фейк записывает путь через фреймы: `iframe[name=mail] >>> button.send`."""


def framed(selector: str, frames: tuple[str, ...]) -> str:
    """Полный путь до узла — с фреймами, если они есть."""
    return FRAME_SEPARATOR.join((*frames, selector))


BLIND_PROFILE = BrowserProfile("fake")
SEEING_PROFILE = BrowserProfile("fake-network", network=CapabilityLevel.CAPTURE_VERIFIED)
STATEFUL_PROFILE = BrowserProfile("fake-state", session_state=CapabilityLevel.CAPTURE_VERIFIED)
FETCHING_PROFILE = BrowserProfile("fake-fetch", page_requests=CapabilityLevel.CAPTURE_VERIFIED)
NAVIGATING_PROFILE = BrowserProfile(
    "fake-navigation", navigation_events=CapabilityLevel.CAPTURE_VERIFIED
)
RICH_PROFILE = BrowserProfile("fake-rich", rich_text=CapabilityLevel.CAPTURE_VERIFIED)


@dataclass
class FakeElement:
    """Узел, который записывает всё, что с ним делали, в лог своей страницы.

    `selector` — путь до узла; у узла коллекции — с номером: `table tr[1]`. По этому же
    ключу страница хранит значения полей (`values`) и атрибуты (`attributes`).
    """

    selector: str
    page: FakeDriver

    async def fill(self, value: str) -> None:
        self.page.log.append(f"fill {self.selector} = {value}")
        self.page.values[self.selector] = value

    async def click(self) -> None:
        self.page.log.append(f"click {self.selector}")
        self.page.on_click(self.selector)

    async def hover(self) -> None:
        self.page.log.append(f"hover {self.selector}")

    async def check(self) -> None:
        self.page.log.append(f"check {self.selector}")

    async def uncheck(self) -> None:
        self.page.log.append(f"uncheck {self.selector}")

    async def select(self, option: str) -> None:
        self.page.log.append(f"select {self.selector} = {option}")
        self.page.values[self.selector] = option

    async def press(self, key: str) -> None:
        self.page.log.append(f"press {self.selector} {key}")

    async def text(self) -> str:
        return f"текст {self.selector}"

    async def value(self) -> str:
        return self.page.values.get(self.selector, "")

    async def attribute(self, name: str) -> str | None:
        return self.page.attributes.get(self.selector, {}).get(name)

    async def visible(self) -> bool:
        return True


@dataclass
class FakeDriver:
    """Драйвер без сети: умеет DOM, текст и адрес.

    `latency` (секунды) делает фейк честным про время: с ней `find` отсутствующего
    узла **ждёт** до таймаута, опрашивая `present` с этим шагом, как ждал бы настоящий
    браузер. Без неё (по умолчанию) фейк отвечает сразу — и регрессия, при которой
    признак ждёт вместо цикла, остаётся невидимой.

    Переход (`goto`) пишется в лог, меняет адрес — с учётом `redirects` — и, если адрес
    есть в `pages`, то, что на странице видно.

    Сколько узлов по пути, говорит `counts`; путь из `present` без записи там — один
    узел. Коллекция (`peek_all`) отдаёт узлы с номерами: `table tr[0]`, `table tr[1]`.
    """

    present: set[str] = field(default_factory=set)
    url: str = "https://portal.example.com/companies"
    text: str = "Список компаний"
    log: list[str] = field(default_factory=list)
    latency: float = 0.0
    redirects: dict[str, str] = field(default_factory=dict)
    """Куда сервер уводит с адреса: `{".../companies": ".../login"}`."""
    pages: dict[str, set[str]] = field(default_factory=dict)
    """Что видно по адресу: переход на такой адрес заменяет `present` целиком."""
    counts: dict[str, int] = field(default_factory=dict)
    """Сколько узлов по пути — для коллекций. Ноль — узлов нет, даже если путь в `present`."""
    values: dict[str, str] = field(default_factory=dict)
    """Значения полей по пути узла: `fill` и `select` пишут сюда, `value` читает."""
    attributes: dict[str, dict[str, str]] = field(default_factory=dict)
    """Атрибуты по пути узла: `{"table tr[1]": {"data-id": "c-2"}}`."""

    @property
    def profile(self) -> BrowserProfile:
        return BLIND_PROFILE

    async def find(self, locator: Locator) -> FakeElement | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + locator.patience
        while True:
            found = await self.peek(locator)
            if found is not None:
                return found
            remaining = deadline - loop.time()
            if self.latency == 0 or remaining <= 0:
                return None
            await asyncio.sleep(min(self.latency, remaining))

    async def peek(self, locator: Locator) -> FakeElement | None:
        # Настоящий драйвер всегда ждёт I/O и уступает управление. Без этой уступки
        # задачи не чередуются, и проверки параллельной работы ничего не проверяют.
        await asyncio.sleep(0)
        for selector in locator.selectors:
            path = framed(selector, locator.frames)
            if self._count(path):
                return FakeElement(path, self)
        return None

    async def peek_all(self, locator: Locator) -> list[Element]:
        await asyncio.sleep(0)
        for selector in locator.selectors:
            path = framed(selector, locator.frames)
            count = self._count(path)
            if count:
                return [FakeElement(f"{path}[{index}]", self) for index in range(count)]
        return []

    def _count(self, path: str) -> int:
        return self.counts.get(path, 1 if path in self.present else 0)

    def on_click(self, selector: str) -> None:
        """Реакция страницы на клик. У страницы без сети и переходов её нет."""

    async def location(self) -> str:
        return self.url

    async def page_text(self) -> str:
        return self.text

    async def goto(self, url: str, *, wait: LoadState = "load", within: float) -> None:
        _ = wait, within
        await asyncio.sleep(0)
        self.log.append(f"goto {url}")
        self._arrive(self.redirects.get(url, url))

    def _arrive(self, url: str) -> None:
        """Страница сменилась: новый адрес и то, что на нём видно."""
        self.url = url
        if url in self.pages:
            self.present = set(self.pages[url])


@dataclass
class NetworkFakeDriver(FakeDriver):
    """Драйвер, который видит ответы: то, чего не умеет selenium.

    `replies` — буфер в порядке прихода; `mark` — его длина. `answers` — ответы,
    которые страница получит на клик по такому пути: так фейк воспроизводит
    настоящий порядок «действие → ответ», и водораздел, взятый до действия, их не
    отсекает. Ответ выпускается один раз: второй клик отвечает следующим из списка.
    """

    replies: list[ResponseView] = field(default_factory=list)
    answers: dict[str, list[ResponseView]] = field(default_factory=dict)

    @property
    def profile(self) -> BrowserProfile:
        return SEEING_PROFILE

    def mark(self) -> int:
        return len(self.replies)

    def on_click(self, selector: str) -> None:
        queued = self.answers.get(selector)
        if queued:
            self.replies.append(queued.pop(0))

    async def wait_response(
        self, url_contains: str, *, within: float, since: int = 0
    ) -> ResponseView | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while True:
            await asyncio.sleep(0)
            for reply in reversed(self.replies[since:]):
                if url_contains in reply.url:
                    return reply
            remaining = deadline - loop.time()
            if self.latency == 0 or remaining <= 0:
                return None
            await asyncio.sleep(min(self.latency, remaining))


@dataclass
class NavigatingFakeDriver(FakeDriver):
    """Драйвер, который видит переходы событием: счётчик растёт на каждую смену страницы.

    `links` — куда уводит клик по пути; `delay` — через сколько секунд после клика
    страница сменится. С задержкой фейк воспроизводит главное: сразу после клика ещё
    видна старая страница, и признак, не спросивший `navigated()`, совпадёт на ней.
    """

    links: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    visits: int = 0

    @property
    def profile(self) -> BrowserProfile:
        return NAVIGATING_PROFILE

    def navigations(self) -> int:
        return self.visits

    def _arrive(self, url: str) -> None:
        super()._arrive(url)
        self.visits += 1

    def on_click(self, selector: str) -> None:
        target = self.links.get(selector)
        if target is None:
            return
        if self.delay == 0:
            self._arrive(target)
            return
        asyncio.get_running_loop().call_later(self.delay, self._arrive, target)


@dataclass
class RichTextFakeDriver(FakeDriver):
    """Драйвер, умеющий класть разметку в редактор. Пишет в лог `html <путь> = <разметка>`."""

    @property
    def profile(self) -> BrowserProfile:
        return RICH_PROFILE

    async def set_html(self, locator: Locator, html: str) -> bool:
        found = await self.find(locator)
        if found is None:
            return False
        self.log.append(f"html {found.selector} = {html}")
        return True


@dataclass
class RichTextLiar(FakeDriver):
    """Профиль обещает редактор, метода нет."""

    @property
    def profile(self) -> BrowserProfile:
        return BrowserProfile("rich-liar", rich_text=CapabilityLevel.CAPTURE_VERIFIED)


@dataclass
class FakeContext:
    """Контекст браузера для фейков: состояние, общее для всех его вкладок.

    Несколько `StatefulFakeDriver` с одним `FakeContext` — несколько вкладок одного
    контекста: вход на одной виден остальным, как куки в настоящем браузере.
    `cookie_writes` — каждая запись кук снаружи, чтобы тест мог их сосчитать.
    """

    state: BrowserState = field(default_factory=BrowserState)
    cookie_writes: list[tuple[BrowserCookie, ...]] = field(default_factory=list)

    def put_cookies(self, cookies: tuple[BrowserCookie, ...]) -> None:
        """Кука с тем же именем, доменом и путём заменяется, новая — дописывается."""
        fresh = {(cookie.name, cookie.domain, cookie.path): cookie for cookie in cookies}
        kept = tuple(
            cookie
            for cookie in self.state.cookies
            if (cookie.name, cookie.domain, cookie.path) not in fresh
        )
        self.state = replace(self.state, cookies=(*kept, *fresh.values()))


@dataclass
class StatefulFakeDriver(FakeDriver):
    """Драйвер, умеющий выгрузить состояние сессии и положить куки в свой контекст."""

    context: FakeContext = field(default_factory=FakeContext)

    @property
    def profile(self) -> BrowserProfile:
        return STATEFUL_PROFILE

    @property
    def state(self) -> BrowserState:
        """Состояние контекста страницы — общее с соседними вкладками."""
        return self.context.state

    @state.setter
    def state(self, state: BrowserState) -> None:
        self.context.state = state

    async def export_state(self) -> BrowserState:
        await asyncio.sleep(0)
        return self.context.state

    async def add_cookies(self, cookies: tuple[BrowserCookie, ...]) -> None:
        await asyncio.sleep(0)
        self.context.put_cookies(cookies)
        self.context.cookie_writes.append(cookies)
        self.log.append(f"cookies {len(cookies)}")

    def context_key(self) -> object:
        return self.context


@dataclass
class StatelessLiar(FakeDriver):
    """Профиль обещает состояние сессии, методов нет."""

    @property
    def profile(self) -> BrowserProfile:
        return BrowserProfile("state-liar", session_state=CapabilityLevel.CAPTURE_VERIFIED)


@dataclass
class FetchFakeDriver(FakeDriver):
    """Драйвер, выполняющий запрос «из страницы». Запоминает, что ему передали."""

    reply: PageReply = field(default_factory=lambda: PageReply(status=200, url="https://x/"))
    refuse: str | None = None
    """Причина отказа браузера: так выглядит запрет CORS или обрыв сети."""

    sent: list[tuple[PageRequest, tuple[str, ...]]] = field(default_factory=list)

    @property
    def profile(self) -> BrowserProfile:
        return FETCHING_PROFILE

    async def fetch(self, request: PageRequest, *, frames: tuple[str, ...] = ()) -> PageReply:
        await asyncio.sleep(0)
        self.sent.append((request, frames))
        if self.refuse is not None:
            raise PageFetchError(request.url, self.refuse)
        return self.reply


@dataclass
class FetchLiar(FakeDriver):
    """Профиль обещает запросы из страницы, метода нет."""

    @property
    def profile(self) -> BrowserProfile:
        return BrowserProfile("fetch-liar", page_requests=CapabilityLevel.CAPTURE_VERIFIED)


@dataclass
class LyingDriver(FakeDriver):
    """Профиль обещает сеть, метода нет: ошибка адаптера, а не сценария."""

    @property
    def profile(self) -> BrowserProfile:
        return BrowserProfile("liar", network=CapabilityLevel.CAPTURE_VERIFIED)


__all__ = [
    "BLIND_PROFILE",
    "FETCHING_PROFILE",
    "FRAME_SEPARATOR",
    "NAVIGATING_PROFILE",
    "RICH_PROFILE",
    "SEEING_PROFILE",
    "STATEFUL_PROFILE",
    "FakeContext",
    "FakeDriver",
    "FakeElement",
    "FetchFakeDriver",
    "FetchLiar",
    "LyingDriver",
    "NavigatingFakeDriver",
    "NetworkFakeDriver",
    "RichTextFakeDriver",
    "RichTextLiar",
    "StatefulFakeDriver",
    "StatelessLiar",
    "framed",
]
