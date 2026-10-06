"""Навигация: адрес операции `Browser.goto` и переход как событие.

Открыть адрес умеет любой драйвер — это минимум (`Driver.goto`). А **увидеть переход**
умеет не каждый: playwright и pydoll получают его событием, selenium — только опросом
адреса, и перезагрузку того же адреса опрос не заметит вовсе. Поэтому переход — ось
профиля `Capability.navigation_events`, как сеть.

Переход не ждут внутри признака: признаки смотрят на снимок страницы и не ждут. Вместо
ожидания — водораздел, как у сетевого буфера: драйвер считает переходы главного фрейма,
раннер запоминает счёт до действия, а признак `navigated()` их сравнивает. Так
читается исход входа:

    outcomes(
        when(navigated() & url.contains("/inbox"), to=Mailbox),
        when(navigated() & visible(css("form.login")), to=BadCredentials),
        otherwise=_login_hangs,
    )

Без `navigated()` второй случай совпал бы сразу после клика — на старой странице, где
форма входа ещё видна.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.parse import quote, urljoin, urlsplit

from eazy_sdk_browser.conditions import Sign
from eazy_sdk_browser.errors import BrowserDeclarationError
from eazy_sdk_browser.locators import placeholders
from eazy_sdk_browser.profile import Capability, validate_profile

if TYPE_CHECKING:
    from eazy_sdk_browser.conditions import Observation
    from eazy_sdk_browser.driver import Driver, LoadState


@dataclass(frozen=True, slots=True)
class Navigation:
    """Куда ведёт операция `Browser.goto`: шаблон адреса и событие готовности страницы."""

    url: str
    """Абсолютный адрес или путь от `base_url` клиента; `{имя}` — поле операции."""
    wait: LoadState = "load"


def render_address(template: str, operation: object, base_url: str) -> str:
    """Адрес перехода: поля операции подставлены и закодированы, путь приложен к базе.

    Значение кодируется целиком, как параметр пути в ядре: `/` внутри значения — это
    символ значения, а не разделитель пути. Абсолютный шаблон базу не спрашивает.
    """
    values = {
        name: quote(str(getattr(operation, name)), safe="-._~") for name in placeholders(template)
    }
    address = template.format_map(values)
    if urlsplit(address).scheme:
        return address
    if not base_url:
        msg = (
            f"relative address {template!r} requires a base URL: "
            "AsyncBrowserClient(driver, base_url=...)"
        )
        raise BrowserDeclarationError(msg)
    return urljoin(base_url.rstrip("/") + "/", address.lstrip("/"))


@runtime_checkable
class NavigationAware(Protocol):
    """Драйвер, который видит переходы главного фрейма событием.

    `navigations` — сколько переходов было за жизнь драйвера, монотонно, как `mark()`
    у сетевого буфера. Считаются и переходы внутри документа (`history.pushState`), и
    перезагрузка того же адреса — ровно то, чего не видно опросом `location()`.
    """

    def navigations(self) -> int: ...


def require_navigation(driver: Driver) -> NavigationAware:
    """Убедиться, что драйвер видит переходы событием."""
    validate_profile((Capability.navigation_events,), driver.profile)
    if not isinstance(driver, NavigationAware):
        # Профиль обещал события, метода нет: ошибка адаптера, а не сценария.
        msg = (
            f"{driver.profile.name} объявил {Capability.navigation_events}, "
            "но не реализует navigations"
        )
        raise TypeError(msg)
    return driver


def navigation_mark(driver: Driver) -> int:
    """Водораздел переходов перед действием. Драйвер без событий — нулевой: ему нечего делить."""
    if driver.profile.supports(Capability.navigation_events) and isinstance(
        driver, NavigationAware
    ):
        return driver.navigations()
    return 0


@dataclass(frozen=True, slots=True, repr=False)
class _Navigated(Sign):
    """Признак: после водораздела главный фрейм куда-то перешёл.

    Куда именно — скажут другие признаки (`url.contains`, `visible`); этот отвечает
    только на вопрос «страница уже сменилась или это ещё старая».
    """

    @property
    def label(self) -> str:
        return "navigated"

    @property
    def requires(self) -> tuple[Capability, ...]:
        return (Capability.navigation_events,)

    async def holds(self, page: Observation) -> bool:
        return require_navigation(page.driver).navigations() > page.navigations


def navigated() -> Sign:
    """Страница сменилась после действия: `navigated() & url.contains("/inbox")`."""
    return _Navigated()


__all__ = [
    "Navigation",
    "NavigationAware",
    "navigated",
    "navigation_mark",
    "render_address",
    "require_navigation",
]
