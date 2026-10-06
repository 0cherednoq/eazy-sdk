"""HTTP-запрос, выполненный изнутри страницы.

`network.py` смотрит на ответы, которые страница получила сама. Здесь — обратное
направление: запрос задаём мы, а выполняет его браузер, своими куками, своим TLS,
своим HTTP/2 и своим антиботом. Для сервиса это обращение его же приложения, а не
постороннего клиента.

Отсюда берётся то, за чем к браузеру и приходят: сервису не нужно объяснять, кто мы, —
вход уже пройден страницей. И отсюда же берётся главное ограничение: **у такого запроса
есть источник**. Браузер добавит `Origin` и `Referer` того документа, из которого шёл
вызов, и спросит у сервиса разрешение (CORS), если источник чужой. Поэтому `frames`
здесь не украшение: запрос из фрейма приложения и запрос из верхней страницы — это два
разных запроса, и сервис отвечает на них по-разному.

Чего браузер не умеет, объявлено в профиле обработчика
(`eazy_sdk_browser.integrations.handler`), а не скрыто: порядок и регистр заголовков,
повторяющиеся имена и ручной `Cookie` ему недоступны.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from eazy_sdk_browser.errors import BrowserError
from eazy_sdk_browser.profile import Capability, validate_profile

if TYPE_CHECKING:
    from collections.abc import Mapping

    from eazy_sdk_browser.driver import Driver

FETCH_TIMEOUT = 30.0

type Credentials = Literal["same-origin", "include", "omit"]
"""Кому запрос представляется куками страницы.

`same-origin` — как у самого приложения: своему источнику куки уходят, чужому нет.
`include` шлёт их и чужому, но тогда сервис обязан разрешить это отдельно
(`Access-Control-Allow-Credentials`), — а сервис, который авторизует по токену, такого
разрешения обычно не даёт, и запрос, работавший без кук, начинает падать запретом CORS.
Поэтому умолчание здесь то же, что в самом браузере, а `include` — осознанный выбор.
"""


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Что выполнить. Адрес — абсолютный: у страницы своя база, и она нам не указ."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None
    credentials: Credentials = "same-origin"
    timeout: float = field(default=FETCH_TIMEOUT)


@dataclass(frozen=True, slots=True)
class PageReply:
    """Что ответили. `url` — конечный: браузер мог пройти редиректы сам.

    Заголовков может быть меньше, чем прислал сервис. Если ответ пришёл с чужого
    источника, браузер показывает только разрешённые CORS: `content-type`,
    `content-length`, `cache-control`, `expires`, `last-modified`, `pragma`,
    `content-language`. Остальные видны, лишь если сервис назвал их в
    `Access-Control-Expose-Headers`.
    """

    status: int
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    redirected: bool = False

    def header_map(self) -> Mapping[str, str]:
        return dict(self.headers)


class PageFetchError(BrowserError):
    """Браузер отказался выполнять запрос: сеть, CORS или превышенный срок.

    Это не ответ сервиса с плохим статусом — до ответа дело не дошло. Потому и
    отдельный тип: `502` разбирается объявлением, а вот запрет CORS объявлением не
    лечится, его чинит тот, кто выбирал источник.
    """

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"{url}: {reason}")


@runtime_checkable
class FetchAware(Protocol):
    """Драйвер, умеющий выполнить запрос от имени страницы."""

    async def fetch(self, request: PageRequest, *, frames: tuple[str, ...] = ()) -> PageReply: ...


def require_fetch(driver: Driver) -> FetchAware:
    """Убедиться, что драйвер умеет запросы из страницы."""
    validate_profile((Capability.page_requests,), driver.profile)
    if not isinstance(driver, FetchAware):
        # Профиль обещал, метода нет: ошибка адаптера, а не сценария.
        msg = f"{driver.profile.name} объявил {Capability.page_requests}, но не реализует fetch"
        raise TypeError(msg)
    return driver


__all__ = [
    "FETCH_TIMEOUT",
    "Credentials",
    "FetchAware",
    "PageFetchError",
    "PageReply",
    "PageRequest",
    "require_fetch",
]
