"""Карта элементов: объявление в аннотациях и его сборка для драйвера.

Карта — это класс, где каждое поле объявлено `Annotated[Тип, маркер]`. Маркер знает,
во что превратиться: `Locator` даёт ленивый элемент, `each` — коллекцию, `region` —
подкарту, `CurrentDriver` — сам драйвер. Сборщик про виды маркеров не знает, он вызывает `bind`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast, get_args, get_type_hints, runtime_checkable

from eazy_sdk_browser.errors import BrowserDeclarationError

if TYPE_CHECKING:
    from eazy_sdk_browser.driver import Driver
    from eazy_sdk_browser.locators import Locator


@dataclass(frozen=True, slots=True)
class Timeouts:
    """Сколько ждать элемент, ответ и переход, секунды — то, что объявление не назвало само."""

    element: float
    response: float
    navigation: float


DEFAULT_TIMEOUTS = Timeouts(element=5.0, response=5.0, navigation=30.0)
"""Для локатора, ответа или перехода, которым никто не задал таймаут: ни объявление, ни клиент.

Переход ждётся дольше элемента: страница грузится целиком, со скриптами и шрифтами."""


@runtime_checkable
class Marker(Protocol):
    """Объявление в аннотации, умеющее превратиться в рабочий объект.

    `scoped` нужен вложенным картам: подкарта диалога ищет свои элементы внутри его
    корня, а не по всей странице. `timed` подставляет таймауты клиента туда, где
    объявление их не назвало.
    """

    def bind(self, driver: Driver) -> object: ...

    def scoped(self, prefix: tuple[str, ...]) -> Marker: ...

    def framed(self, frames: tuple[str, ...]) -> Marker: ...

    def timed(self, timeouts: Timeouts) -> Marker: ...


def build_content[TContent](
    declaration: type[TContent],
    driver: Driver,
    scope: tuple[str, ...] = (),
    frames: tuple[str, ...] = (),
    timeouts: Timeouts = DEFAULT_TIMEOUTS,
) -> TContent:
    """Собрать объявленную карту для конкретного драйвера.

    Аннотация без маркера — опечатка в объявлении, а не повод молча пропустить поле.
    """
    content = object.__new__(cast(type[Any], declaration))
    for name, annotation in get_type_hints(declaration, include_extras=True).items():
        markers = [argument for argument in get_args(annotation) if isinstance(argument, Marker)]
        if not markers:
            msg = f"{declaration.__qualname__}.{name}: аннотация без маркера"
            raise BrowserDeclarationError(msg)
        marker = markers[0]
        if scope:
            marker = marker.scoped(scope)
        if frames:
            marker = marker.framed(frames)
        marker = marker.timed(timeouts)
        object.__setattr__(content, name, marker.bind(driver))
    return cast(TContent, content)


@dataclass(frozen=True, slots=True)
class CurrentDriver:
    """Сам драйвер в карте — когда исход уносит с собой доступ к странице."""

    def bind(self, driver: Driver) -> Driver:
        return driver

    def scoped(self, prefix: tuple[str, ...]) -> CurrentDriver:
        """Драйвер один на страницу — корень подкарты на него не влияет."""
        _ = prefix
        return self

    def framed(self, frames: tuple[str, ...]) -> CurrentDriver:
        """Драйвер один на страницу — фрейм операции на него не влияет."""
        _ = frames
        return self

    def timed(self, timeouts: Timeouts) -> CurrentDriver:
        _ = timeouts
        return self


@dataclass(frozen=True, slots=True)
class Region[TCard]:
    """Подкарта, привязанная к корню.

    Корнем берётся первый селектор `root`: список кандидатов на корне означал бы, что
    подкарта может оказаться в разных местах разом, а это уже не одна область.
    """

    card: type[TCard]
    root: Locator
    timeouts: Timeouts = DEFAULT_TIMEOUTS

    def bind(self, driver: Driver) -> TCard:
        return build_content(
            self.card,
            driver,
            scope=(self.root.selectors[0],),
            frames=self.root.frames,
            timeouts=self.timeouts,
        )

    def scoped(self, prefix: tuple[str, ...]) -> Region[TCard]:
        """Вложенная подкарта: корни складываются от внешнего к внутреннему."""
        return Region(self.card, self.root.scoped(prefix))

    def framed(self, frames: tuple[str, ...]) -> Region[TCard]:
        """Подкарта уезжает во фрейм вместе со своим корнем."""
        return Region(self.card, self.root.framed(frames), self.timeouts)

    def timed(self, timeouts: Timeouts) -> Region[TCard]:
        """Таймауты клиента доходят до каждого поля подкарты."""
        return Region(self.card, self.root, timeouts)


def region[TCard](card: type[TCard], *, root: Locator) -> Region[TCard]:
    """Объявить часть страницы подкартой.

    Так объявляется то, что **уже есть в DOM**: панель фильтров, строка таблицы.
    Форма, которой ещё нет (диалог по кнопке), — не подкарта, а отдельное состояние:
    у неё своё условие готовности, и попасть в него можно только операцией открытия.
    """
    return Region(card, root)
