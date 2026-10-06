"""Протокол драйвера: минимум, который обязан уметь любой адаптер.

Всё, что умеет не каждый драйвер — сеть, переход как событие, выгрузка сессии,
редактор — объявляется возможностью (`eazy_sdk_browser.profile`), а не добавляется
сюда. Иначе половина адаптеров держала бы заглушки на методы, которых не умеет.
Открыть адрес умеет любой драйвер, поэтому `goto` — здесь.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from eazy_sdk_browser.locators import Locator
    from eazy_sdk_browser.profile import BrowserProfile

type LoadState = Literal["load", "domcontentloaded", "networkidle"]
"""До какого события страницы ждёт переход — как `wait_until` у playwright."""


@runtime_checkable
class Element(Protocol):
    """Элемент страницы. Одинаков и для узла драйвера, и для ленивой ссылки на него."""

    async def fill(self, value: str) -> None: ...

    async def click(self) -> None: ...

    async def hover(self) -> None:
        """Навести указатель: меню и подсказки появляются только так."""
        ...

    async def check(self) -> None: ...

    async def uncheck(self) -> None: ...

    async def select(self, option: str) -> None:
        """Выбрать пункт выпадающего списка по значению или подписи."""
        ...

    async def press(self, key: str) -> None: ...

    async def text(self) -> str: ...

    async def value(self) -> str:
        """Значение поля — то, что уйдёт с формой, а не то, что видно в разметке."""
        ...

    async def attribute(self, name: str) -> str | None:
        """Атрибут узла. `None` — атрибута нет; пустая строка — есть, но пустой."""
        ...

    async def visible(self) -> bool: ...


@runtime_checkable
class Driver(Protocol):
    """Страница глазами операции.

    Два способа искать, потому что у них разные вызывающие. `find` ждёт появления
    любого из кандидатов и нужен действиям: клик по кнопке, которой ещё нет, — это
    ожидание. `peek` отвечает сразу и нужен признакам: цикл ожидания сам придёт
    снова, а признак, который ждёт внутри себя, съедает чужое время.

    Оба получают локатор целиком: кандидаты, какой из совпавших брать, фреймы,
    таймаут в секундах. Адаптер ждёт **любого** из кандидатов разом, иначе каждый
    отсутствующий стоил бы полного таймаута.

    `None` — если видимого узла не нашлось: ненайденный элемент — обычный исход
    поиска, а не авария транспорта.

    `peek_all` отдаёт коллекцию: все видимые узлы первого кандидата, у которого они
    есть. Тоже без ожидания — пустая таблица такой же ответ, как полная.
    """

    @property
    def profile(self) -> BrowserProfile:
        """Что этот драйвер умеет. Проверяется до запуска сценария."""
        ...

    async def find(self, locator: Locator) -> Element | None: ...

    async def peek(self, locator: Locator) -> Element | None: ...

    async def peek_all(self, locator: Locator) -> Sequence[Element]:
        """Видимые узлы первого кандидата, у которого они есть, — в порядке документа.

        Кандидаты не смешиваются: строки старой и новой вёрстки в одной коллекции дали
        бы таблицу, которой на странице нет. `pick` здесь не участвует.
        """
        ...

    async def goto(self, url: str, *, wait: LoadState = "load", within: float) -> None:
        """Открыть адрес и дождаться события `wait` — не дольше `within` секунд.

        Не открылся — сеть отказала, срок вышел, страница закрыта — это `TransportError`
        ядра: до страницы дело не дошло, и объявлениям страницы разбирать тут нечего.
        """
        ...

    async def location(self) -> str: ...

    async def page_text(self) -> str: ...
