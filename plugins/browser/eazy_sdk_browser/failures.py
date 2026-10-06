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

Отказ, приходящий **ответом API** (`409`, `403`), объявляется не здесь, а разбором
ответа: статус и модель — дело `eazy_sdk_browser.network` и его декодера.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from eazy_sdk_browser.errors import BrowserError

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
    exception: type[Exception] | ExceptionFactory

    async def check(self, page: Observation) -> None:
        """Бросить объявленное исключение, если признак наблюдается."""
        if not await self.when.holds(page):
            return
        if isinstance(self.exception, type):
            raise self.exception
        raise await self.exception(page)


type FailureRules = tuple[Failure, ...]


async def enforce(rules: FailureRules, page: Observation) -> None:
    """Проверить правила по порядку. Первое совпавшее и определяет отказ.

    Порядок объявления — это приоритет: частные правила ставят выше общих, иначе
    «что-то пошло не так» перехватит «неверный пароль».
    """
    for rule in rules:
        await rule.check(page)


__all__ = [
    "DetailReader",
    "ExceptionFactory",
    "Failure",
    "FailureRules",
    "PageError",
    "enforce",
    "text_of",
]
