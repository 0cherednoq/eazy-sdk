"""Язык признаков: чем страница себя выдаёт.

Один язык и для исходов действия, и для правил отказа — иначе создателю SDK пришлось
бы держать в голове два способа сказать «видно вот это».

Фабрики в нижнем регистре, как условия ответа в `eazy-sdk`
(`body.contains(...) | status.is_(...)`):

    visible(css("div[role=dialog]"))
    text.contains("Недостаточно прав") | url.contains("/login")
    ~visible(css("div.spinner"))

У каждого признака есть `label` — так он читается в диагностике, когда исход не
наступил или наступили два разом.

Признак **не ждёт**. Он смотрит на страницу такой, какая она сейчас, и отвечает
сразу; ждать — дело цикла, который опрашивает все признаки по кругу. Признак, ждущий
внутри себя, съел бы время у остальных, и второй исход стал бы недостижим.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eazy_sdk_browser.driver import Driver, Element
    from eazy_sdk_browser.locators import Locator
    from eazy_sdk_browser.profile import Capability


@dataclass(slots=True)
class Observation:
    """Страница на одном круге опроса: то, что видно прямо сейчас.

    Признаки получают её, а не драйвер: за круг адрес и текст читаются один раз,
    сколько бы признаков на них ни смотрело. `since` — водораздел сетевого буфера:
    ответы, пришедшие до действия, к этому действию не относятся. `handled` —
    обработчики помех, уже сработавшие за эту операцию: снимок нового круга берёт
    его у прежнего (`next_round`), чтобы `once=True` считался на всю операцию.
    `navigations` — водораздел переходов: счёт драйвера до того, что операция сделала
    сама (`eazy_sdk_browser.navigation`).
    """

    driver: Driver
    since: int = 0
    navigations: int = 0
    handled: set[int] = field(default_factory=set)
    _location: str | None = field(default=None, init=False)
    _text: str | None = field(default=None, init=False)

    async def location(self) -> str:
        if self._location is None:
            self._location = await self.driver.location()
        return self._location

    async def page_text(self) -> str:
        if self._text is None:
            self._text = await self.driver.page_text()
        return self._text

    async def peek(self, locator: Locator) -> Element | None:
        """Есть ли элемент сейчас. Без ожидания — ждёт цикл, а не признак."""
        return await locator.peek(self.driver)

    def next_round(self) -> Observation:
        """Свежий снимок той же операции: кэш сброшен, водораздел и `handled` те же."""
        return Observation(
            self.driver, since=self.since, navigations=self.navigations, handled=self.handled
        )


class Sign(ABC):
    """Наблюдаемый признак страницы."""

    __slots__ = ()

    @property
    @abstractmethod
    def label(self) -> str:
        """Как признак читается в диагностике."""

    @property
    def requires(self) -> tuple[Capability, ...]:
        """Возможности драйвера, без которых признак не наблюдается.

        Раннер сверяет их с профилем до действия вместе с `requires=` операции: признак
        сети в правилах роутера отсекает драйвер без сети до клика, а не посреди ожидания.
        """
        return ()

    @abstractmethod
    async def holds(self, page: Observation) -> bool:
        """Наблюдается ли признак прямо сейчас."""

    def __or__(self, other: Sign) -> Sign:
        return _AnyOf((self, other))

    def __and__(self, other: Sign) -> Sign:
        return _AllOf((self, other))

    def __invert__(self) -> Sign:
        return _Not(self)

    def __repr__(self) -> str:
        return f"<{self.label}>"


@dataclass(frozen=True, slots=True, repr=False)
class _Visible(Sign):
    """Элемент найден на странице."""

    locator: Locator

    @property
    def label(self) -> str:
        return f"visible {self.locator.label}"

    async def holds(self, page: Observation) -> bool:
        return await page.peek(self.locator) is not None


@dataclass(frozen=True, slots=True, repr=False)
class _TextContains(Sign):
    """Текст страницы содержит фрагмент — так порталы сообщают об отказе."""

    fragment: str
    ignore_case: bool

    @property
    def label(self) -> str:
        return f"text contains {self.fragment!r}" + (" ignoring case" if self.ignore_case else "")

    async def holds(self, page: Observation) -> bool:
        text = await page.page_text()
        if self.ignore_case:
            return self.fragment.casefold() in text.casefold()
        return self.fragment in text


@dataclass(frozen=True, slots=True, repr=False)
class _UrlContains(Sign):
    """Адрес содержит маркер: форма входа объясняет исход редиректом."""

    marker: str

    @property
    def label(self) -> str:
        return f"url contains {self.marker!r}"

    async def holds(self, page: Observation) -> bool:
        return self.marker in await page.location()


@dataclass(frozen=True, slots=True, repr=False)
class _UrlMatches(Sign):
    """Адрес подходит под регулярное выражение."""

    pattern: re.Pattern[str]

    @property
    def label(self) -> str:
        return f"url matches {self.pattern.pattern!r}"

    async def holds(self, page: Observation) -> bool:
        return self.pattern.search(await page.location()) is not None


@dataclass(frozen=True, slots=True, repr=False)
class _AnyOf(Sign):
    """Хотя бы один из признаков."""

    signs: tuple[Sign, ...]

    @property
    def label(self) -> str:
        return "(" + " or ".join(sign.label for sign in self.signs) + ")"

    @property
    def requires(self) -> tuple[Capability, ...]:
        return _joined(self.signs)

    async def holds(self, page: Observation) -> bool:
        for sign in self.signs:
            if await sign.holds(page):
                return True
        return False


@dataclass(frozen=True, slots=True, repr=False)
class _AllOf(Sign):
    """Все признаки сразу."""

    signs: tuple[Sign, ...]

    @property
    def label(self) -> str:
        return "(" + " and ".join(sign.label for sign in self.signs) + ")"

    @property
    def requires(self) -> tuple[Capability, ...]:
        return _joined(self.signs)

    async def holds(self, page: Observation) -> bool:
        for sign in self.signs:
            if not await sign.holds(page):
                return False
        return True


@dataclass(frozen=True, slots=True, repr=False)
class _Not(Sign):
    """Признака нет."""

    sign: Sign

    @property
    def label(self) -> str:
        return f"not {self.sign.label}"

    @property
    def requires(self) -> tuple[Capability, ...]:
        return self.sign.requires

    async def holds(self, page: Observation) -> bool:
        return not await self.sign.holds(page)


def _joined(signs: tuple[Sign, ...]) -> tuple[Capability, ...]:
    """Возможности составного признака: всех частей, без повторов, в порядке объявления."""
    return tuple(dict.fromkeys(capability for sign in signs for capability in sign.requires))


def visible(locator: Locator) -> Sign:
    """Элемент виден на странице."""
    return _Visible(locator)


class _Text:
    """Признаки по видимому тексту страницы. Используется объект `text`."""

    __slots__ = ()

    def contains(self, fragment: str, *, ignore_case: bool = True) -> Sign:
        """Текст страницы содержит фрагмент.

        Регистр по умолчанию не важен: порталы пишут «Недостаточно прав» и
        «НЕДОСТАТОЧНО ПРАВ» одинаково охотно.
        """
        return _TextContains(fragment, ignore_case)


class _Url:
    """Признаки по адресу страницы. Используется объект `url`."""

    __slots__ = ()

    def contains(self, marker: str) -> Sign:
        """Адрес содержит подстроку: `url.contains("/login")`."""
        return _UrlContains(marker)

    def matches(self, pattern: str | re.Pattern[str]) -> Sign:
        """Адрес подходит под регулярное выражение."""
        return _UrlMatches(re.compile(pattern) if isinstance(pattern, str) else pattern)


text = _Text()
url = _Url()

__all__ = ["Observation", "Sign", "text", "url", "visible"]
