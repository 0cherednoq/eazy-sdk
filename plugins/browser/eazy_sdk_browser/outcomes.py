"""Исходы действия: признак на странице и то, что он означает.

`outcomes(...)` собирает union исходов из самой декларации: типизатор выводит его из
случаев и `otherwise`, поэтому `reveal_type` показывает ровно те состояния, которые
объявлены, — как `Responses(success=..., errors=...)` в `eazy-sdk` показывает модели
ответа.

    outcomes(
        when(visible(css("div[role=dialog]")), to=CreateCompanyDialog),
        when(url.contains("/login"), to=LoginPage),
        otherwise=_dialog_missing,
    )

Ожидание идёт по кругу с общим дедлайном, который приходит из опций вызова, а не из
объявления: сколько ждать — свойство вызова, а не операции.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast, overload

from eazy_sdk_browser.errors import BrowserError
from eazy_sdk_browser.failures import enforce
from eazy_sdk_browser.interceptors import handle

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from eazy_sdk_browser.api import AsyncBrowserApi
    from eazy_sdk_browser.conditions import Observation, Sign
    from eazy_sdk_browser.failures import FailureRules
    from eazy_sdk_browser.interceptors import Handlers
    from eazy_sdk_browser.operations import Call

POLL_INTERVAL = 0.05


class FromApi(Protocol):
    """Состояние, которое строится из роутера и больше ни из чего.

    Допустимые операции состояния — операции роутера, который оно держит: диалог,
    открытый `portal.open_create_company()`, отправляется `self.api.submit_company()`.
    """

    def __init__(self, api: AsyncBrowserApi) -> None: ...


@dataclass(frozen=True, slots=True)
class When[TContent, TResult]:
    """Признак на странице и исход, который он означает.

    Исход задаётся одним из двух способов. `then` — фабрика от карты: так строят
    данные, прочитанные со страницы. `to` — класс состояния: так выражают переход,
    и тогда состояние создаёт раннер из роутера, через который операция и вызвана.
    """

    sign: Sign
    then: Callable[[TContent], Awaitable[TResult]] | None = None
    to: type[TResult] | None = None

    def __post_init__(self) -> None:
        if (self.then is None) == (self.to is None):
            msg = "у исхода должен быть ровно один способ построения: then или to"
            raise ValueError(msg)

    async def build(self, content: TContent, api: AsyncBrowserApi | None) -> TResult:
        """Построить исход тем способом, которым он объявлен."""
        if self.to is not None:
            if api is None:
                msg = (
                    f"outcome {self.to.__name__} is a state and needs the router that ran "
                    "the operation; call it through AsyncBrowserApi, not execute()"
                )
                raise BrowserError(msg)
            # Что класс принимает роутер, гарантирует перегрузка `when(to=...)`:
            # она ограничена протоколом `FromApi`. Здесь это уже не выразить —
            # `When` параметризован типом исхода, а не способом его постройки.
            state = cast("Callable[[AsyncBrowserApi], TResult]", self.to)
            return state(api)
        assert self.then is not None  # noqa: S101 — гарантировано __post_init__
        return await self.then(content)


@overload
def when[TContent, TState: FromApi](sign: Sign, *, to: type[TState]) -> When[TContent, TState]: ...


@overload
def when[TContent, TResult](
    sign: Sign, *, then: Callable[[TContent], Awaitable[TResult]]
) -> When[TContent, TResult]: ...


def when(
    sign: Sign,
    *,
    then: Callable[[Any], Awaitable[Any]] | None = None,
    to: type[Any] | None = None,
) -> When[Any, Any]:
    """Объявить: увидели такой признак — значит исход такой.

    `to=Mailbox` читается как `to: ResultsPage` в Geb: клик уводит в состояние, и
    строить его вручную не нужно — а значит, и тащить драйвер через карту элементов.
    """
    return When(sign, then, to)


@dataclass(frozen=True, slots=True)
class Outcomes[TContent, TResult]:
    """Все исходы действия разом: что ждём и что делаем, если не дождались."""

    cases: tuple[When[TContent, Any], ...]
    otherwise: Callable[[TContent], Awaitable[TResult]]

    async def settle(
        self,
        content: TContent,
        page: Observation,
        failures: FailureRules,
        handlers: Handlers,
        call: Call,
    ) -> TResult:
        """Дождаться признака и построить исход; не дождались — исход по умолчанию.

        Опрос идёт по кругу, а не ожиданием каждого признака по очереди: иначе первый
        же кандидат съел бы весь таймаут и второй исход стал бы недостижим. Поэтому
        признаки смотрят на снимок страницы (`Observation`) и не ждут сами.

        Порядок внутри круга не случаен. Сначала убираются помехи: баннер согласия
        перекрывает и признак отказа, и признак исхода, поэтому смотреть на страницу
        поверх него бессмысленно. Потом правила отказа — иначе отказ дождался бы конца
        таймаута и выглядел бы неопределённостью. И только потом исходы.

        `page` — снимок, взятый до действия: отсюда берутся драйвер, водораздел сети и
        уже сработавшие обработчики (`once=True` считается на всю операцию), а на
        каждый круг делается свежий снимок. `call.within` — общий дедлайн, секунды;
        `call.api` — роутер, из которого строятся состояния.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + call.within
        while True:
            view = page.next_round()
            if await handle(handlers, view):
                continue
            await enforce(failures, view)
            for case in self.cases:
                if await case.sign.holds(view):
                    # Случаи лежат в кортеже как `When[TContent, Any]`: у каждого свой
                    # тип исхода, а `TResult` — их объединение, и сузить это здесь нечем.
                    return cast("TResult", await case.build(content, call.api))
            if loop.time() >= deadline:
                return await self.otherwise(content)
            await asyncio.sleep(POLL_INTERVAL)


@overload
def outcomes[TContent, A, B, C, D, E, F, Z](
    a: When[TContent, A],
    b: When[TContent, B],
    c: When[TContent, C],
    d: When[TContent, D],
    e: When[TContent, E],
    f: When[TContent, F],
    /,
    *,
    otherwise: Callable[[TContent], Awaitable[Z]],
) -> Outcomes[TContent, A | B | C | D | E | F | Z]: ...


@overload
def outcomes[TContent, A, B, C, D, E, Z](
    a: When[TContent, A],
    b: When[TContent, B],
    c: When[TContent, C],
    d: When[TContent, D],
    e: When[TContent, E],
    /,
    *,
    otherwise: Callable[[TContent], Awaitable[Z]],
) -> Outcomes[TContent, A | B | C | D | E | Z]: ...


@overload
def outcomes[TContent, A, B, C, D, Z](
    a: When[TContent, A],
    b: When[TContent, B],
    c: When[TContent, C],
    d: When[TContent, D],
    /,
    *,
    otherwise: Callable[[TContent], Awaitable[Z]],
) -> Outcomes[TContent, A | B | C | D | Z]: ...


@overload
def outcomes[TContent, A, B, C, Z](
    a: When[TContent, A],
    b: When[TContent, B],
    c: When[TContent, C],
    /,
    *,
    otherwise: Callable[[TContent], Awaitable[Z]],
) -> Outcomes[TContent, A | B | C | Z]: ...


@overload
def outcomes[TContent, A, B, Z](
    a: When[TContent, A],
    b: When[TContent, B],
    /,
    *,
    otherwise: Callable[[TContent], Awaitable[Z]],
) -> Outcomes[TContent, A | B | Z]: ...


@overload
def outcomes[TContent, A, Z](
    a: When[TContent, A],
    /,
    *,
    otherwise: Callable[[TContent], Awaitable[Z]],
) -> Outcomes[TContent, A | Z]: ...


@overload
def outcomes[TContent](
    *cases: When[TContent, Any],
    otherwise: Callable[[TContent], Awaitable[Any]],
) -> Outcomes[TContent, Any]: ...


def outcomes(
    *cases: When[Any, Any],
    otherwise: Callable[[Any], Awaitable[Any]],
) -> Outcomes[Any, Any]:
    """Собрать набор исходов; тип результата типизатор выводит из самой декларации.

    До шести случаев union выводится точно; дальше — `Any`, и тогда тип результата
    задаётся параметром операции.
    """
    return Outcomes(cases, otherwise)


__all__ = ["POLL_INTERVAL", "FromApi", "Outcomes", "When", "outcomes", "when"]
