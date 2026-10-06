"""Сетевые ответы страницы: признак прихода и разбор объявлением.

Портал часто объясняет исход не вёрсткой, а ответом своего же API: форма молчит, а в
XHR лежит `409 {"code": "company_exists"}`. Ответ браузера — обычный HTTP-ответ,
поэтому разбирать его должно то же объявление, что и в HTTP-SDK: статус, медиа-тип,
модель, типизированное исключение. Своего языка для этого не заводим — `Decoder`
принимает **весь ответ**, а не голое тело, и реализуется адаптером
(`eazy_sdk_browser.integrations.eazy_sdk`).

Сеть видят не все драйверы: playwright и pydoll умеют через CDP, selenium — нет.
Поэтому ответ объявляется маркером карты `ApiResponse`, а операция — `requires=
(Capability.network,)`, и на драйвере без сети она падает `CapabilityMismatchError`
до первого действия. Одно без другого — ошибка объявления (D-B-04).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from eazy_sdk_browser.conditions import Sign
from eazy_sdk_browser.content import DEFAULT_TIMEOUTS, Timeouts
from eazy_sdk_browser.errors import BrowserError
from eazy_sdk_browser.profile import Capability, validate_profile

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from eazy_sdk_browser.conditions import Observation
    from eazy_sdk_browser.driver import Driver


@dataclass(frozen=True, slots=True)
class ResponseView:
    """Сетевой ответ, каким его увидел браузер.

    Статус и заголовки здесь не для полноты: без них нельзя ни выбрать случай по коду,
    ни отличить JSON от страницы входа, вернувшейся вместо него.
    """

    url: str
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()
    body_dropped: bool = False
    """Тело больше лимита захвата и не прочитано: `body` пуст не потому, что пуст ответ."""

    def header_map(self) -> Mapping[str, str]:
        return dict(self.headers)


@runtime_checkable
class NetworkAware(Protocol):
    """Драйвер, умеющий показать ответы, которые получила страница.

    Буфер ответов растёт монотонно: `mark` — позиция в нём прямо сейчас, а
    `wait_response(..., since=)` не смотрит на ответы, пришедшие раньше позиции.
    Без водораздела вторая отправка той же формы прочитала бы ответ на первую.
    """

    def mark(self) -> int: ...

    async def wait_response(
        self, url_contains: str, *, within: float, since: int = 0
    ) -> ResponseView | None: ...


def require_network(driver: Driver) -> NetworkAware:
    """Убедиться, что драйвер видит сеть. Без этого разбор ответа бессмыслен."""
    validate_profile((Capability.network,), driver.profile)
    if not isinstance(driver, NetworkAware):
        # Профиль обещал сеть, но метода нет: это ошибка адаптера, а не сценария.
        msg = f"{driver.profile.name} объявил {Capability.network}, но не реализует wait_response"
        raise TypeError(msg)
    return driver


def network_mark(driver: Driver) -> int:
    """Водораздел буфера перед действием. Драйвер без сети — нулевой: ему нечего делить."""
    if driver.profile.supports(Capability.network) and isinstance(driver, NetworkAware):
        return driver.mark()
    return 0


class Decoder[T](Protocol):
    """Как превратить ответ в модель — или в объявленное исключение.

    Получает ответ целиком: разбор по статусу невозможен, если декодеру видно только
    тело. Реализуется адаптером `eazy-sdk` или простым `json_as`.
    """

    def decode(self, response: ResponseView) -> T: ...


@dataclass(frozen=True, slots=True)
class JsonAs[T]:
    """Разбор без проверок: любой ответ считается успешным JSON-объектом.

    Самый примитивный случай: не смотрит ни на статус, ни на тип содержимого, поэтому
    `500` с HTML-страницей ошибки превратит в `TypeError` посреди сценария. Настоящий
    разбор — `eazy_sdk_browser.integrations.eazy_sdk.sdk_cases`.
    """

    model: Callable[..., T]

    def decode(self, response: ResponseView) -> T:
        payload = json.loads(response.body)
        if not isinstance(payload, dict):
            msg = f"ожидался JSON-объект, пришло {type(payload).__name__}"
            raise TypeError(msg)
        fields: Mapping[str, object] = payload
        return self.model(**fields)


def json_as[T](model: Callable[..., T]) -> JsonAs[T]:
    """Объявить разбор тела ответа в модель, не глядя на статус."""
    return JsonAs(model)


@dataclass(frozen=True, slots=True, repr=False)
class _Arrived(Sign):
    """Признак: на действие пришёл ответ по такому адресу.

    Только факт прихода. Что ответ означает, решает объявление в `Decoder`: иначе
    условие исхода и разбор ответа начали бы спорить между собой.

    Ответ либо уже в буфере, либо нет — признак не ждёт, за ним придёт цикл. И только
    ответ после водораздела: прошлый ответ на тот же адрес — не про это действие.
    """

    url_contains: str

    @property
    def label(self) -> str:
        return f"response arrived {self.url_contains!r}"

    @property
    def requires(self) -> tuple[Capability, ...]:
        return (Capability.network,)

    async def holds(self, page: Observation) -> bool:
        network = require_network(page.driver)
        arrived = await network.wait_response(self.url_contains, within=0, since=page.since)
        return arrived is not None


class _Response:
    """Признаки по ответам API страницы. Используется объект `response`."""

    __slots__ = ()

    def arrived(self, url_contains: str) -> Sign:
        """На действие пришёл ответ по адресу: `response.arrived("/api/companies")`."""
        return _Arrived(url_contains)


response = _Response()


class ResponseMissingError(BrowserError):
    """Ответа, на который сослался исход, в браузере не оказалось — или нет его тела."""

    def __init__(self, url_contains: str, reason: str = "") -> None:
        self.url_contains = url_contains
        self.reason = reason
        super().__init__(f"{url_contains}: {reason}" if reason else url_contains)


@dataclass(frozen=True, slots=True)
class ApiValue[T]:
    """Разобранный ответ API, привязанный к драйверу.

    Лежит в карте наравне с элементами: признак говорит, что ответ пришёл, а исход
    берёт из него данные. Объявленный отказ поднимется отсюда исключением.
    """

    driver: Driver
    url_contains: str
    decoder: Decoder[T]
    timeout: float
    since: int
    """Водораздел на момент сборки карты — то есть до действия операции."""

    async def value(self) -> T:
        """Ответ, разобранный объявлением."""
        network = require_network(self.driver)
        found = await network.wait_response(
            self.url_contains, within=self.timeout, since=self.since
        )
        if found is None:
            raise ResponseMissingError(self.url_contains)
        if found.body_dropped:
            # Разбирать пустоту нельзя: декодер принял бы её за ответ без тела.
            raise ResponseMissingError(self.url_contains, "тело больше лимита захвата")
        return self.decoder.decode(found)


@dataclass(frozen=True, slots=True)
class ApiResponse[T]:
    """Маркер карты: ответ API, разобранный объявлением.

    Драйвер держит уже пришедшие ответы, поэтому признак и исход, читающие один и тот
    же ответ, не ждут его дважды.
    """

    url_contains: str
    decoder: Decoder[T]
    timeout: float | None = None
    """Секунды. `None` — таймаут ответа из конфигурации клиента."""

    @property
    def requires(self) -> tuple[Capability, ...]:
        """Без сети ответ не прочитать — операция обязана объявить её (D-B-04)."""
        return (Capability.network,)

    def bind(self, driver: Driver) -> ApiValue[T]:
        """Привязка запоминает позицию буфера.

        Карта собирается до действия, и всё, что пришло раньше, ответом на это
        действие быть не может.
        """
        timeout = DEFAULT_TIMEOUTS.response if self.timeout is None else self.timeout
        return ApiValue(driver, self.url_contains, self.decoder, timeout, network_mark(driver))

    def scoped(self, prefix: tuple[str, ...]) -> ApiResponse[T]:
        """Сеть не принадлежит области страницы — корень подкарты на неё не влияет."""
        _ = prefix
        return self

    def framed(self, frames: tuple[str, ...]) -> ApiResponse[T]:
        """Запросы фрейма видны драйверу наравне с остальными."""
        _ = frames
        return self

    def timed(self, timeouts: Timeouts) -> ApiResponse[T]:
        """Таймаут ответа из клиента, если объявление своего не назвало."""
        if self.timeout is not None:
            return self
        return ApiResponse(self.url_contains, self.decoder, timeouts.response)


__all__ = [
    "ApiResponse",
    "ApiValue",
    "Decoder",
    "JsonAs",
    "NetworkAware",
    "ResponseMissingError",
    "ResponseView",
    "json_as",
    "network_mark",
    "require_network",
    "response",
]
