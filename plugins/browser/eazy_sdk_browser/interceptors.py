"""Обработчики помех: увидел признак — сделай действие и продолжай.

Зеркальная вещь к контракту отказов. `Failure` говорит «увидел — сдавайся с этим
исключением», `Handle` — «увидел — убери и работай дальше»:

    COOKIE_BANNER = Handle(
        when=visible(css(ACCEPT, frames=CONSENT_FRAMES)),
        do=click(css(ACCEPT, frames=CONSENT_FRAMES)),
    )

    __browser__ = Browser.act(CompaniesPage, handlers=(COOKIE_BANNER,))

Проверяются **первыми** в цикле ожидания, до правил отказа и до исходов: баннер
перекрывает и то, и другое, поэтому сначала убирают помеху, а потом смотрят, что
на странице на самом деле.

`once=True` (по умолчанию) означает «сработать один раз за операцию»: иначе
обработчик, который не смог убрать помеху, крутился бы до конца таймаута. Операция
— это и действие, и ожидание исхода: баннер, убранный до клика, второй раз не ищут.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from eazy_sdk_browser.conditions import Observation, Sign
    from eazy_sdk_browser.driver import Driver
    from eazy_sdk_browser.locators import Locator

type Action = Callable[[Driver], Awaitable[None]]


def click(locator: Locator) -> Action:
    """Действие: нажать на элемент."""

    async def act(driver: Driver) -> None:
        await locator.bind(driver).click()

    return act


@dataclass(frozen=True, slots=True)
class Handle:
    """Признак помехи и то, что с ней делать."""

    when: Sign
    do: Action
    once: bool = True

    async def apply(self, page: Observation) -> bool:
        """Сработал ли обработчик. `page.handled` помнит отработавшие за операцию."""
        if self.once and id(self) in page.handled:
            return False
        if not await self.when.holds(page):
            return False
        await self.do(page.driver)
        page.handled.add(id(self))
        return True


type Handlers = tuple[Handle, ...]


async def handle(handlers: Handlers, page: Observation) -> bool:
    """Убрать первую же встреченную помеху. `True` — что-то сделали."""
    for handler in handlers:
        if await handler.apply(page):
            return True
    return False
