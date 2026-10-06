"""Спека браузерной операции и фасад `Browser` — единственный dunder операции.

Та же форма, что `__http__ = Http.post(...)` в `eazy-sdk`: всё объявление операции
лежит в одном значении, а не в россыпи `__at__`, `__content__`, `__scope__`, `__frame__`,
`__errors__`, `__handlers__`. Поля сгруппированы, как в `_HttpSpec`: где операция
работает, что приходит назад, что ей нужно от драйвера, как она называется.

    @dataclass(frozen=True, slots=True, kw_only=True)
    class OpenCreateCompany(BrowserOperation[CompaniesPage, CreateCompanyDialog]):
        __browser__ = Browser.act(
            CompaniesPage,
            at=css('button[data-test="create"]'),
            outcomes=outcomes(
                when(visible(css('div[role="dialog"]')), to=CreateCompanyDialog),
                otherwise=_dialog_missing,
            ),
        )

        async def act(self, content: CompaniesPage) -> None:
            await content.create_button.click()

Переход — второй глагол. Метода `act` у такой операции нет: её тело — сам переход.

    @dataclass(frozen=True, slots=True, kw_only=True)
    class OpenCompany(BrowserOperation[None, None]):
        __browser__ = Browser.goto("/companies/{company_id}", at=css("h1.company"))

        company_id: str
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict, Unpack, overload

from eazy_sdk_browser.navigation import Navigation

if TYPE_CHECKING:
    from eazy_sdk_browser.driver import LoadState
    from eazy_sdk_browser.failures import FailureRules
    from eazy_sdk_browser.interceptors import Handlers
    from eazy_sdk_browser.locators import Locator
    from eazy_sdk_browser.outcomes import Outcomes
    from eazy_sdk_browser.profile import Capability

_NO_CONTENT: type[None] = type(None)
"""Карта операции, которой карта не нужна: `Browser.goto(url)` без класса карты."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _BrowserSpec[TContent]:
    """Всё объявление операции. Строится фасадом `Browser`, руками не собирается."""

    content: type[TContent]
    # Где операция работает.
    navigation: Navigation | None = None
    """Адрес, который операция открывает (`Browser.goto`). `None` — операция действует на
    уже открытой странице (`Browser.act`)."""
    at: Locator | None = None
    """Условие готовности: до него шаги бьются вслепую. У `act` проверяется до действия,
    у `goto` — на открытой странице. Проверяет раннер, не автор."""
    scope: Locator | None = None
    """Корень, внутри которого ищутся элементы карты: `button[type=submit]` есть и в
    форме фильтров, и в диалоге, и без корня клик уйдёт не туда."""
    frames: tuple[str, ...] = ()
    """Фреймы, внутри которых живёт карта, от корня страницы вглубь."""
    # Что приходит назад.
    outcomes: Outcomes[TContent, Any] | None = None
    """Исходы действия. Без них операция возвращает `None`: сделала — и всё."""
    errors: FailureRules = ()
    """Правила отказа операции. Проверяются раньше правил роутера."""
    inherit_errors: bool = True
    """Добавлять ли правила роутера после своих."""
    handlers: Handlers = ()
    """Обработчики помех: баннеры и слои, которые надо убрать, чтобы добраться до дела."""
    # Что операции нужно от драйвера.
    requires: tuple[Capability, ...] = ()
    """Возможности, без которых операция бессмысленна; сверяются с профилем до действия."""
    # Имена для инструментов.
    operation_id: str | None = None
    tags: tuple[str, ...] = ()


class _BrowserOptions[TContent](TypedDict, total=False):
    at: Locator | None
    scope: Locator | None
    frames: tuple[str, ...]
    outcomes: Outcomes[TContent, Any] | None
    errors: FailureRules
    inherit_errors: bool
    handlers: Handlers
    requires: tuple[Capability, ...]
    operation_id: str | None
    tags: tuple[str, ...]


class Browser:
    """Фасад объявления: `__browser__ = Browser.act(...)` или `Browser.goto(...)`.

    Два глагола — по одному на то, чем операция является. `act` — действие на уже
    открытой странице, тело — метод `act`. `goto` — переход по адресу, и тело — сам
    переход: метода `act` у такой операции нет (D-B-09).
    """

    __slots__ = ()

    @staticmethod
    def act[TContent](
        content: type[TContent], /, **options: Unpack[_BrowserOptions[TContent]]
    ) -> _BrowserSpec[TContent]:
        """Операция с телом: условие готовности, карта, исходы, правила."""
        return _BrowserSpec(content=content, **options)

    @overload
    @staticmethod
    def goto(
        url: str, /, *, wait: LoadState = "load", **options: Unpack[_BrowserOptions[None]]
    ) -> _BrowserSpec[None]: ...

    @overload
    @staticmethod
    def goto[TContent](
        url: str,
        content: type[TContent],
        /,
        *,
        wait: LoadState = "load",
        **options: Unpack[_BrowserOptions[TContent]],
    ) -> _BrowserSpec[TContent]: ...

    @staticmethod
    def goto(
        url: str,
        content: type[Any] = _NO_CONTENT,
        /,
        *,
        wait: LoadState = "load",
        **options: Unpack[_BrowserOptions[Any]],
    ) -> _BrowserSpec[Any]:
        """Операция-переход: открыть адрес, дождаться готовности, распознать исход.

        `url` — абсолютный адрес или путь от `base_url` клиента; `{имя}` подставляет поле
        операции, закодированным целиком. Карта нужна, только если исходы читают
        страницу, — без неё операция объявляется `BrowserOperation[None, ...]`.
        """
        return _BrowserSpec(content=content, navigation=Navigation(url, wait), **options)


__all__ = ["Browser"]
