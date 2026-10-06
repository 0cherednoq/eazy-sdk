"""Язык локаторов: где искать элемент и что делать, если вёрстка поменялась.

`Locator`, а не `Query`: в `eazy-sdk` `Query` — маркер поля строки запроса, и
одноимённый класс с другим смыслом в соседнем пакете стоил бы каждому SDK, который
импортирует оба, псевдонима при импорте.
"""

from __future__ import annotations

import asyncio
import string
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from eazy_sdk_browser.content import DEFAULT_TIMEOUTS, Timeouts
from eazy_sdk_browser.errors import BrowserDeclarationError, BrowserError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from eazy_sdk_browser.driver import Driver, Element

NTH_POLL = 0.05
"""Шаг, с которым узел коллекции по номеру ждёт, пока узлов станет достаточно."""


class Pick(StrEnum):
    """Какой из совпавших узлов берём, если селектор нашёл несколько."""

    first = "first"
    last = "last"


@dataclass(frozen=True, slots=True)
class Locator:
    """Где искать элемент: кандидаты по порядку, первый найденный — наш.

    Список, а не один селектор, потому что вёрстка живых страниц меняется, и
    запасной селектор дешевле упавшего сценария.
    """

    selectors: tuple[str, ...]
    pick: Pick = Pick.first
    timeout: float | None = None
    """Секунды. `None` — таймаут элемента из конфигурации клиента, а не объявления."""
    # Цепочка селекторов iframe от корня страницы. Пустая — искать на самой странице.
    # Без неё не автоматизировать ни одну почту: и GMX, и ARMGS держат интерфейс во фрейме.
    frames: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.selectors:
            msg = "локатор без селекторов ничего не найдёт"
            raise ValueError(msg)

    @property
    def patience(self) -> float:
        """Сколько адаптеру ждать, секунды: свой таймаут или умолчание библиотеки."""
        return DEFAULT_TIMEOUTS.element if self.timeout is None else self.timeout

    @property
    def label(self) -> str:
        """Как локатор читается в диагностике: кандидаты через `|`, фреймы — впереди."""
        inside = " | ".join(self.selectors)
        return " >>> ".join((*self.frames, inside)) if self.frames else inside

    def bind(self, driver: Driver) -> Lazy:
        return Lazy(driver, self)

    async def find(self, driver: Driver) -> Element | None:
        """Дождаться любого кандидата.

        Одним вызовом: ждать их по очереди — платить полный таймаут за каждого
        отсутствующего.
        """
        return await driver.find(self)

    async def peek(self, driver: Driver) -> Element | None:
        """Есть ли кандидат прямо сейчас — без ожидания."""
        return await driver.peek(self)

    def scoped(self, prefix: tuple[str, ...]) -> Locator:
        """Тот же локатор, но искать внутри корня подкарты."""
        if not prefix:
            return self
        root = " ".join(prefix)
        return replace(self, selectors=tuple(f"{root} {selector}" for selector in self.selectors))

    def framed(self, frames: tuple[str, ...]) -> Locator:
        """Тот же локатор, но внутри фреймов операции. Свои фреймы локатора идут глубже."""
        if not frames:
            return self
        return replace(self, frames=frames + self.frames)

    def timed(self, timeouts: Timeouts) -> Locator:
        """Тот же локатор с таймаутом клиента, если объявление своего не назвало."""
        if self.timeout is not None:
            return self
        return replace(self, timeout=timeouts.element)


def css(selector: str, *, timeout: float | None = None, frames: tuple[str, ...] = ()) -> Locator:
    """Один селектор, первое совпадение."""
    return Locator((selector,), timeout=timeout, frames=frames)


def last(selector: str, *, timeout: float | None = None, frames: tuple[str, ...] = ()) -> Locator:
    """Один селектор, последнее совпадение: диалог открывается поверх прежних."""
    return Locator((selector,), pick=Pick.last, timeout=timeout, frames=frames)


def any_of(*selectors: str, timeout: float | None = None, frames: tuple[str, ...] = ()) -> Locator:
    """Кандидаты по порядку: берём первый, который нашёлся."""
    return Locator(selectors, timeout=timeout, frames=frames)


def placeholders(template: str) -> tuple[str, ...]:
    """Имена `{полей}` в шаблоне селектора или адреса — только простые имена.

    `{company.id}`, `{0}` и `{id!r}` отвергаются: значение подставляет и экранирует
    библиотека, и форматирование внутри шаблона ей бы мешало.
    """
    names: list[str] = []
    for _, name, format_spec, conversion in string.Formatter().parse(template):
        if name is None:
            continue
        if not name.isidentifier() or format_spec or conversion:
            msg = "placeholders must be plain field names, without conversion or format"
            raise ValueError(msg)
        names.append(name)
    return tuple(names)


def css_escape(value: str) -> str:
    """Значение для CSS-селектора — по алгоритму `CSS.escape` из CSSOM.

    Годится и для идентификатора (`#{id}`), и для строки в кавычках
    (`[data-name="{name}"]`): экранированная последовательность в CSS-строке означает тот же
    символ. Кавычка, обратная косая черта и управляющие символы селектор больше не ломают.
    """
    return "".join(_escape_code_point(value, index, char) for index, char in enumerate(value))


def _escape_code_point(value: str, index: int, char: str) -> str:
    code = ord(char)
    if code == 0:
        return "\ufffd"
    if code <= 0x1F or code == 0x7F or _leading_digit(value, index, char):
        return f"\\{code:x} "
    if index == 0 and char == "-" and len(value) == 1:
        return "\\-"
    if code >= 0x80 or char in "-_" or (char.isascii() and char.isalnum()):
        return char
    return "\\" + char


def _leading_digit(value: str, index: int, char: str) -> bool:
    """Цифра в начале идентификатора — или сразу после ведущего дефиса."""
    if not "0" <= char <= "9":
        return False
    return index == 0 or (index == 1 and value[0] == "-")


def text_escape(value: str) -> str:
    """Значение для текстовых движков playwright в кавычках: `text="…"`, `:has-text("…")`.

    Пробельные символы схлопываются в один пробел — текстовое сравнение делает то же
    самое, — а обратная косая черта и кавычка экранируются: такую строку понимает и разбор
    `text="…"`, и CSS-строка внутри `:has-text()`.
    """
    collapsed = " ".join(value.split())
    return collapsed.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True, slots=True)
class Template:
    """Элемент, который ищется по параметру: строка таблицы, письмо с такой темой.

    Селектор карты статичен, а искать часто нужно по значению — теме письма, названию
    компании. Параметр подставляется в объявленный шаблон **экранированным**: кавычка в
    названии компании не ломает селектор и не меняет его смысл.
    """

    driver: Driver
    pattern: Locator
    escape: Callable[[str], str] = css_escape

    def __call__(self, **values: object) -> Lazy:
        """Подставить значения в шаблон селектора. Имена — ровно те, что в шаблоне."""
        names = {name for selector in self.pattern.selectors for name in placeholders(selector)}
        if names != set(values):
            msg = (
                f"template {self.pattern.label} takes {', '.join(sorted(names))}, "
                f"got {', '.join(sorted(values))}"
            )
            raise TypeError(msg)
        escaped = {name: self.escape(str(value)) for name, value in values.items()}
        return Lazy(
            self.driver,
            replace(
                self.pattern,
                selectors=tuple(
                    selector.format_map(escaped) for selector in self.pattern.selectors
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class TemplateLocator:
    """Маркер карты: элемент с параметром и способом экранировать значение."""

    pattern: Locator
    escape: Callable[[str], str] = css_escape

    def bind(self, driver: Driver) -> Template:
        return Template(driver, self.pattern, self.escape)

    def scoped(self, prefix: tuple[str, ...]) -> TemplateLocator:
        return replace(self, pattern=self.pattern.scoped(prefix))

    def framed(self, frames: tuple[str, ...]) -> TemplateLocator:
        return replace(self, pattern=self.pattern.framed(frames))

    def timed(self, timeouts: Timeouts) -> TemplateLocator:
        return replace(self, pattern=self.pattern.timed(timeouts))


def template(
    *selectors: str, timeout: float | None = None, frames: tuple[str, ...] = ()
) -> TemplateLocator:
    """Объявить элемент, который ищется по параметру в CSS-селекторе.

        row: Annotated[Template, template('tr[data-name="{name}"]')]

    Значение экранируется по `CSS.escape`. Для текстовых движков playwright (`text=`,
    `:has-text()`) — `template_text`: там другие правила строки.
    """
    return _template(selectors, css_escape, timeout, frames)


def template_text(
    *selectors: str, timeout: float | None = None, frames: tuple[str, ...] = ()
) -> TemplateLocator:
    """Объявить элемент, который ищется по тексту в текстовом движке playwright.

        letter: Annotated[Template, template_text('text="{subject}"')]

    Значение экранируется как строка в кавычках (`text_escape`), а не по `CSS.escape`:
    тот экранировал бы пробелы, и тема письма перестала бы совпадать. Кавычки вокруг места
    подстановки пишет автор шаблона.
    """
    return _template(selectors, text_escape, timeout, frames)


def _template(
    selectors: tuple[str, ...],
    escape: Callable[[str], str],
    timeout: float | None,
    frames: tuple[str, ...],
) -> TemplateLocator:
    """Шаблон проверяется при объявлении, а не при первом вызове на живой странице."""
    for selector in selectors:
        try:
            placeholders(selector)
        except ValueError as error:
            msg = f"template {selector!r}: {error}"
            raise BrowserDeclarationError(msg) from error
    return TemplateLocator(Locator(selectors, timeout=timeout, frames=frames), escape)


class ElementNotFoundError(BrowserError):
    """Ни один кандидат не нашёлся: интерфейс страницы изменился.

    `index` — номер узла коллекции, если искали его: строк меньше, чем ожидалось.
    """

    def __init__(self, locator: Locator, *, index: int | None = None) -> None:
        self.locator = locator
        self.index = index
        super().__init__(locator.label if index is None else f"{locator.label} [{index}]")


@dataclass(frozen=True, slots=True)
class Lazy:
    """Ссылка на элемент, которая ищется в момент действия, а не объявления.

    Благодаря ей карта элементов объявляется на уровне класса — тогда, когда
    никакой страницы ещё нет. `index` — узел коллекции по номеру (`Elements.nth`).
    """

    driver: Driver
    locator: Locator
    index: int | None = None

    async def fill(self, value: str) -> None:
        await (await self.resolve()).fill(value)

    async def click(self) -> None:
        await (await self.resolve()).click()

    async def hover(self) -> None:
        await (await self.resolve()).hover()

    async def check(self) -> None:
        await (await self.resolve()).check()

    async def uncheck(self) -> None:
        await (await self.resolve()).uncheck()

    async def select(self, option: str) -> None:
        await (await self.resolve()).select(option)

    async def press(self, key: str) -> None:
        """Нажать клавишу в элементе: иногда Enter надёжнее клика по кнопке."""
        await (await self.resolve()).press(key)

    async def text(self) -> str:
        return await (await self.resolve()).text()

    async def value(self) -> str:
        return await (await self.resolve()).value()

    async def attribute(self, name: str) -> str | None:
        return await (await self.resolve()).attribute(name)

    async def visible(self) -> bool:
        """Нашёлся ли хоть один кандидат. Ненайденный элемент — обычный исход поиска."""
        try:
            await self.resolve()
        except ElementNotFoundError:
            return False
        return True

    async def resolve(self) -> Element:
        """Первый появившийся кандидат — или узел коллекции с номером `index`."""
        if self.index is not None:
            return await self._nth(self.index)
        found = await self.locator.find(self.driver)
        if found is None:
            raise ElementNotFoundError(self.locator)
        return found

    async def _nth(self, index: int) -> Element:
        """Узел по номеру. Ждёт, пока узлов станет достаточно, — как `find` ждёт один.

        Номер как у списка: `-1` — последний. Ожидание — опросом `peek_all`: номер узла
        среди видимых драйвер одним событием не выразит.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.locator.patience
        while True:
            nodes = await self.driver.peek_all(self.locator)
            if -len(nodes) <= index < len(nodes):
                return nodes[index]
            if loop.time() >= deadline:
                raise ElementNotFoundError(self.locator, index=index)
            await asyncio.sleep(NTH_POLL)


@dataclass(frozen=True, slots=True)
class Elements:
    """Коллекция узлов: строки таблицы, письма в списке, пункты меню.

    Читается снимком на момент вызова и не ждёт: пустая таблица — такой же ответ, как
    полная, а дождаться строк — дело условия готовности или исхода. Действие над
    `nth(i)` ждёт, как действие над любым элементом.
    """

    driver: Driver
    locator: Locator

    async def count(self) -> int:
        """Сколько видимых узлов сейчас."""
        return len(await self.driver.peek_all(self.locator))

    def nth(self, index: int) -> Lazy:
        """Узел по номеру среди видимых, от нуля; `-1` — последний. Ищется в момент действия."""
        return Lazy(self.driver, self.locator, index)

    async def texts(self) -> list[str]:
        """Тексты всех узлов по порядку — колонка таблицы одним вызовом."""
        return [await node.text() for node in await self.driver.peek_all(self.locator)]

    def __aiter__(self) -> AsyncIterator[Element]:
        """Обход снимка: узлы, видимые на момент начала обхода."""
        return self._walk()

    async def _walk(self) -> AsyncIterator[Element]:
        for node in await self.driver.peek_all(self.locator):
            yield node


@dataclass(frozen=True, slots=True)
class EachLocator:
    """Маркер карты: все узлы локатора, а не первый."""

    pattern: Locator

    def bind(self, driver: Driver) -> Elements:
        return Elements(driver, self.pattern)

    def scoped(self, prefix: tuple[str, ...]) -> EachLocator:
        return EachLocator(self.pattern.scoped(prefix))

    def framed(self, frames: tuple[str, ...]) -> EachLocator:
        return EachLocator(self.pattern.framed(frames))

    def timed(self, timeouts: Timeouts) -> EachLocator:
        return EachLocator(self.pattern.timed(timeouts))


def each(locator: Locator) -> EachLocator:
    """Объявить коллекцию — все видимые узлы локатора, а не первый.

        rows: Annotated[Elements, each(css("table.companies tr"))]

    Из кандидатов `any_of` берётся первый, у которого есть видимые узлы; `pick` у
    коллекции не участвует. Таймаут локатора — сколько ждёт действие над `nth(i)`.
    """
    return EachLocator(locator)


__all__ = [
    "NTH_POLL",
    "EachLocator",
    "ElementNotFoundError",
    "Elements",
    "Lazy",
    "Locator",
    "Pick",
    "Template",
    "TemplateLocator",
    "any_of",
    "css",
    "css_escape",
    "each",
    "last",
    "placeholders",
    "template",
    "template_text",
    "text_escape",
]
