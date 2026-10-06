"""Состояние браузерной сессии: куки и localStorage — без привязки к драйверу.

Вход в сервис стоит дорого: капча, антибот, второй фактор. Поэтому состояние сессии
выгружают после успешного входа, чтобы следующий контекст пришёл на сайт уже
«знакомым». localStorage кладёт в контекст тот, кто его создаёт (`storage_state` при
создании контекста — `handlers.playwright.to_storage_state`); в живой контекст
пишутся только куки, и пишет их `BrowserSession`.

Здесь описано, **что** такое это состояние, а не как его хранить. Куда его положить —
дело вызывающего: файл, база, хранилище аккаунтов `eazy-sdk`
(`eazy_sdk_browser.integrations.accounts`). Ядру об этих способах знать нечего.

Умеют не все: playwright и camoufox выгружают контекст целиком, у selenium штатного
способа нет. Поэтому доступ идёт через `Capability.session_state`, и сценарий, которому
состояние нужно, падает до первого действия, а не после входа.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from eazy_sdk_browser.profile import Capability, validate_profile

if TYPE_CHECKING:
    from datetime import datetime

    from eazy_sdk_browser.driver import Driver


@dataclass(frozen=True, slots=True)
class BrowserCookie:
    """Кука со всеми атрибутами, а не парой «имя-значение».

    `BrowserCookie`, а не `Cookie`: в `eazy-sdk` `Cookie` — маркер поля запроса.

    Атрибуты здесь не для полноты: без `domain` и `path` браузер не примет куку
    обратно, а без `expires_at` не отличит сессионную от долгой. Ровно поэтому пара
    «имя-значение» для переноса сессии не годится.
    """

    name: str
    value: str = field(repr=False)
    domain: str = ""
    path: str = "/"
    expires_at: datetime | None = None
    secure: bool = False
    http_only: bool = False
    same_site: str = ""

    def is_session(self) -> bool:
        """Сессионная кука: живёт до закрытия браузера, срока у неё нет."""
        return self.expires_at is None


@dataclass(frozen=True, slots=True)
class Origin:
    """localStorage одного источника.

    Источник, а не страница: браузер держит хранилище на пару «схема + хост», и
    восстанавливать его надо туда же, иначе оно достанется чужому сайту.
    """

    origin: str
    items: tuple[tuple[str, str], ...] = ()

    def mapping(self) -> dict[str, str]:
        return dict(self.items)


@dataclass(frozen=True, slots=True)
class BrowserState:
    """Всё, что делает браузер «уже входившим»: куки и localStorage."""

    cookies: tuple[BrowserCookie, ...] = ()
    origins: tuple[Origin, ...] = ()

    def is_empty(self) -> bool:
        return not self.cookies and not self.origins


@runtime_checkable
class StateAware(Protocol):
    """Драйвер, умеющий выгрузить состояние сессии, положить куки и назвать свой контекст.

    Записи localStorage здесь нет: в живой контекст её можно сделать только стартовым
    скриптом, который срабатывает на каждой навигации и затирает то, что сайт обновил.
    """

    async def export_state(self) -> BrowserState:
        """Куки и localStorage контекста страницы."""
        ...

    async def add_cookies(self, cookies: tuple[BrowserCookie, ...]) -> None:
        """Положить куки в контекст страницы. Повторная запись той же куки безвредна."""
        ...

    def context_key(self) -> object:
        """Непрозрачный ключ контекста: равен у всех страниц одного контекста.

        Сравнивается только на равенство — чтобы объект одного контекста не приняли за
        объект другого.
        """
        ...


def require_state(driver: Driver) -> StateAware:
    """Убедиться, что драйвер умеет состояние сессии.

    Возвращает узкий вид на драйвер: три операции вместо всего транспорта.
    Отдельного фасада поверх, в отличие от `Network`, нет — добавлять ему нечего.
    """
    validate_profile((Capability.session_state,), driver.profile)
    if not isinstance(driver, StateAware):
        # Профиль обещал состояние, но методов нет: ошибка адаптера, а не сценария.
        msg = (
            f"{driver.profile.name} объявил {Capability.session_state},"
            " но не реализует export_state/add_cookies/context_key"
        )
        raise TypeError(msg)
    return driver


__all__ = ["BrowserCookie", "BrowserState", "Origin", "StateAware", "require_state"]
