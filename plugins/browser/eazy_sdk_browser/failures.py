"""Контракт отказов страницы: что считать отказом, объявляет создатель SDK.

Строится по образцу `eazy-sdk`, где разбор ответов объявлен один раз на сервис
(`errors = SERVICE_ERRORS`). Здесь то же самое, только признак берётся со страницы:

    PORTAL_ERRORS = (
        Failure(when=text.contains("Недостаточно прав"), exception=_access_denied),
        Failure(when=url.contains("/login"), exception=SessionExpiredError),
    )

    __browser__ = Browser.act(CompaniesPage, errors=PORTAL_ERRORS)

`exception=` — класс или фабрика `async (page) -> Exception`, как фабрики исключений
в `Error(..., exception=)` ядра: фабрика читает подробности со снимка страницы —
`text_of(locator)` помогает взять их из элемента.

Подробности, которые на странице лежат несколькими элементами, объявляются картой, как
у исходов, а не ищутся в фабрике руками:

    class RejectedDialog:
        reasons: Annotated[Elements, each(css("p.error b"))]

    async def _rejected(dialog: RejectedDialog) -> Exception:
        return RecipientRejected(await dialog.reasons.texts())

    Failure(when=text.contains("Невозможно"), reads=RejectedDialog, exception=_rejected)

С `reads=` фабрика получает собранную карту, а не снимок страницы.

Отказ, приходящий **ответом API** (`409`, `403`), объявляется не здесь, а разбором
ответа: статус и модель — дело `eazy_sdk_browser.network` и его декодера.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from eazy_sdk_browser.content import build_content
from eazy_sdk_browser.errors import BrowserDeclarationError, BrowserError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from eazy_sdk_browser.conditions import Observation, Sign
    from eazy_sdk_browser.locators import Locator


class PageError(BrowserError):
    """Отказ, который создатель SDK объявил заранее.

    Отличается от неожиданного сбоя тем, что он **назван**: вызывающий ловит
    конкретный класс, а не разбирает строку сообщения.
    """

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or type(self).__doc__ or type(self).__name__)


type DetailReader = Callable[[Observation], Awaitable[str]]
type ExceptionFactory = Callable[[Observation], Awaitable[Exception]]
type ContentFactory = Callable[[Any], Awaitable[Exception] | Exception]
"""Фабрика правила с `reads=`: получает собранную карту, может быть синхронной."""


def text_of(locator: Locator) -> DetailReader:
    """Взять подробности отказа из элемента страницы, а не из имени класса.

    Помощник для фабрики исключения: `AccessDeniedError(await text_of(css(...))(page))`.
    Без ожидания: отказ уже наблюдается, и если элемента с подробностями нет прямо
    сейчас, подробностей просто нет.
    """

    async def read(page: Observation) -> str:
        found = await page.peek(locator)
        return "" if found is None else await found.text()

    return read


@dataclass(frozen=True, slots=True)
class Failure:
    """Признак → исключение. Единица контракта отказов."""

    when: Sign
    exception: type[Exception] | ExceptionFactory | ContentFactory
    reads: type[object] | None = None
    """Карта элементов с подробностями отказа: фабрика получает её, а не снимок страницы.

    Собирается без ожидания готовности: отказ уже наблюдается. Элементы ищутся по всей
    странице — правило не знает ни корня, ни фреймов операции."""

    def __post_init__(self) -> None:
        if self.reads is None:
            return
        if isinstance(self.exception, type):
            msg = (
                f"Failure(reads={self.reads.__name__}) с классом {self.exception.__name__}: "
                "карту читает фабрика, а класс исключения ничего не читает"
            )
            raise BrowserDeclarationError(msg)
        build_content(self.reads, cast("Any", None))  # карта без маркера видна при объявлении

    async def check(self, page: Observation) -> None:
        """Бросить объявленное исключение, если признак наблюдается."""
        if not await self.when.holds(page):
            return
        if isinstance(self.exception, type):
            raise self.exception
        if self.reads is None:
            raise await cast("ExceptionFactory", self.exception)(page)
        built = cast("ContentFactory", self.exception)(build_content(self.reads, page.driver))
        raise (await built) if inspect.isawaitable(built) else built


type FailureRules = tuple[Failure, ...]


async def enforce(rules: FailureRules, page: Observation) -> None:
    """Проверить правила по порядку. Первое совпавшее и определяет отказ.

    Порядок объявления — это приоритет: частные правила ставят выше общих, иначе
    «что-то пошло не так» перехватит «неверный пароль».
    """
    for rule in rules:
        await rule.check(page)


__all__ = [
    "ContentFactory",
    "DetailReader",
    "ExceptionFactory",
    "Failure",
    "FailureRules",
    "PageError",
    "enforce",
    "text_of",
]
