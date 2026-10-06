"""Форматированный текст: разметка в редактор, а не символы в поле.

Письмо, комментарий, описание компании — это `contenteditable`, а не `<input>`. Набрать
туда разметку клавишами нельзя: она встанет текстом. Её нужно положить узлу и сообщить
редактору событием `input` — а это умеет не каждый драйвер. Поэтому редактор —
возможность `Capability.rich_text`, а не минимум `Element`:

    class Compose:
        body: Annotated[RichText, rich(css('div[contenteditable="true"]'))]

    __browser__ = Browser.act(Compose, requires=(Capability.rich_text,))

Операция обязана объявить возможность (D-B-04), и драйвер без неё отсекается до первого
действия; карта, собранная для такого драйвера напрямую, отказывает при сборке.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from eazy_sdk_browser.locators import ElementNotFoundError, Lazy, Locator
from eazy_sdk_browser.profile import Capability, validate_profile

if TYPE_CHECKING:
    from eazy_sdk_browser.content import Timeouts
    from eazy_sdk_browser.driver import Driver


@runtime_checkable
class RichTextAware(Protocol):
    """Драйвер, умеющий положить разметку в редактор."""

    async def set_html(self, locator: Locator, html: str) -> bool:
        """Дождаться узла, как `find`, положить разметку и поднять `input`. `False` — узла нет."""
        ...


def require_rich_text(driver: Driver) -> RichTextAware:
    """Убедиться, что драйвер умеет редактор."""
    validate_profile((Capability.rich_text,), driver.profile)
    if not isinstance(driver, RichTextAware):
        # Профиль обещал, метода нет: ошибка адаптера, а не сценария.
        msg = f"{driver.profile.name} объявил {Capability.rich_text}, но не реализует set_html"
        raise TypeError(msg)
    return driver


@dataclass(frozen=True, slots=True)
class RichText(Lazy):
    """Редактор в карте: всё, что умеет элемент, и `set_html` сверху."""

    async def set_html(self, html: str) -> None:
        """Положить разметку в редактор. Узла нет — `ElementNotFoundError`, как у действий."""
        if not await require_rich_text(self.driver).set_html(self.locator, html):
            raise ElementNotFoundError(self.locator)


@dataclass(frozen=True, slots=True)
class RichLocator:
    """Маркер карты: редактор форматированного текста."""

    pattern: Locator

    @property
    def requires(self) -> tuple[Capability, ...]:
        """Без возможности редактора поле бессмысленно — операция обязана её объявить."""
        return (Capability.rich_text,)

    def bind(self, driver: Driver) -> RichText:
        """Драйвер без возможности отказывает здесь — при сборке карты, до первого действия."""
        require_rich_text(driver)
        return RichText(driver, self.pattern)

    def scoped(self, prefix: tuple[str, ...]) -> RichLocator:
        return RichLocator(self.pattern.scoped(prefix))

    def framed(self, frames: tuple[str, ...]) -> RichLocator:
        return RichLocator(self.pattern.framed(frames))

    def timed(self, timeouts: Timeouts) -> RichLocator:
        return RichLocator(self.pattern.timed(timeouts))


def rich(locator: Locator) -> RichLocator:
    """Объявить редактор форматированного текста.

    body: Annotated[RichText, rich(css('div[contenteditable="true"]'))]
    """
    return RichLocator(locator)


__all__ = ["RichLocator", "RichText", "RichTextAware", "require_rich_text", "rich"]
