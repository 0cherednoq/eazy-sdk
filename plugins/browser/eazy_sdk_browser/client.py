"""Клиент: драйвер плюс конфигурация, как `AsyncClient` — обработчик плюс `ClientConfig`.

Роутеры получают клиент, а не драйвер: сколько ждать исход, элемент, ответ или переход —
свойство клиента и вызова, а не объявления. Объявление говорит, **что** ждать. База
адресов — тоже клиента: `Browser.goto("/companies")` не знает, на каком стенде портал.

    async with AsyncBrowserClient(
        PlaywrightDriver(page), base_url="https://portal.example.com", owns_driver=True
    ) as client:
        portal = CompaniesPortal(client)
        await portal.open_companies()
        dialog = await portal.open_create_company()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, Self

from eazy_sdk_browser.content import DEFAULT_TIMEOUTS, Timeouts
from eazy_sdk_browser.errors import BrowserDeclarationError
from eazy_sdk_browser.operations import Call, declaration_of, run

if TYPE_CHECKING:
    from eazy_sdk_browser.api import AsyncBrowserApi
    from eazy_sdk_browser.driver import Driver
    from eazy_sdk_browser.operations import BrowserOperation
    from eazy_sdk_browser.spec import _BrowserSpec


@dataclass(frozen=True, slots=True)
class BrowserClientConfig:
    """Таймауты по умолчанию, секунды. Объявление локатора или ответа переопределяет их."""

    timeout: float = 5.0
    """Общий дедлайн ожидания исхода после действия."""
    element_timeout: float = DEFAULT_TIMEOUTS.element
    """Сколько ждать появления элемента при действии над ним."""
    response_timeout: float = DEFAULT_TIMEOUTS.response
    """Сколько ждать ответа API, на который ссылается карта."""
    navigation_timeout: float = DEFAULT_TIMEOUTS.navigation
    """Сколько ждать, пока откроется адрес операции `Browser.goto`."""
    auth_retries: int = 1
    """Сколько раз повторить операцию после отказа-истечения сессии — как у `ClientConfig`.

    Повтор имеет смысл для перехода (`Browser.goto`): действие на странице входа второй раз
    не удастся, и отказ всплывёт уже как неготовность."""

    def __post_init__(self) -> None:
        if self.auth_retries < 0:
            msg = "auth_retries cannot be negative"
            raise BrowserDeclarationError(msg)


@dataclass(frozen=True, slots=True)
class BrowserCallOptions:
    """Что можно поменять у одного вызова, не трогая объявление.

    Как `CallOptions` в ядре: единственный зарезервированный аргумент вызова.
    `None` — взять из конфигурации клиента.
    """

    timeout: float | None = None
    element_timeout: float | None = None
    response_timeout: float | None = None
    navigation_timeout: float | None = None


def resolve_timeouts(config: BrowserClientConfig, options: BrowserCallOptions | None) -> Timeouts:
    """Таймауты элемента, ответа и перехода: вызов важнее конфигурации."""
    chosen = BrowserCallOptions() if options is None else options
    return Timeouts(
        element=_either(chosen.element_timeout, config.element_timeout),
        response=_either(chosen.response_timeout, config.response_timeout),
        navigation=_either(chosen.navigation_timeout, config.navigation_timeout),
    )


def resolve_settle(config: BrowserClientConfig, options: BrowserCallOptions | None) -> float:
    """Дедлайн ожидания исхода: вызов важнее конфигурации."""
    if options is None or options.timeout is None:
        return config.timeout
    return options.timeout


def _either(own: float | None, fallback: float) -> float:
    return fallback if own is None else own


def call_of(
    config: BrowserClientConfig,
    options: BrowserCallOptions | None,
    api: AsyncBrowserApi | None = None,
    base_url: str = "",
) -> Call:
    """Контекст выполнения из конфигурации клиента и опций вызова."""
    return Call(resolve_timeouts(config, options), resolve_settle(config, options), api, base_url)


async def execute[TContent, TResult](
    operation: BrowserOperation[TContent, TResult],
    driver: Driver,
    *,
    base_url: str = "",
    config: BrowserClientConfig | None = None,
    options: BrowserCallOptions | None = None,
) -> TResult:
    """Выполнить операцию на драйвере без роутера: спека — как объявлена в классе.

    Состояния-исходы (`to=`) отсюда не построить — им нужен роутер; такие операции
    вызываются через `AsyncBrowserApi`.
    """
    settings = BrowserClientConfig() if config is None else config
    return await run(
        operation,
        driver,
        declaration_of(type(operation)),
        call_of(settings, options, base_url=base_url),
    )


class SessionSource[TRevision](Protocol):
    """Всё, что клиент знает о сессии: есть ли она в браузере и что делать после отказа.

    Ревизия для клиента непрозрачна: он её только держит и отдаёт обратно в `renew`. Без
    пула источник — `BrowserSession` (`BrowserLogin.session(...)`), с пулом — источник
    пула, который не входит сам: его `renew` отдаёт `None`, и отказ сайта уходит наружу.
    """

    def is_expired(self, error: BaseException) -> bool:
        """Значит ли этот отказ операции, что сессия кончилась."""
        ...

    async def ensure(self, client: AsyncBrowserClient) -> TRevision:
        """Сессия в браузере перед операцией; вернуть её ревизию."""
        ...

    async def renew(self, client: AsyncBrowserClient, rejected: TRevision) -> TRevision | None:
        """Сервис отверг `rejected`: новая ревизия — или `None`, если повторять нечем."""
        ...


class AsyncBrowserClient:
    """Драйвер, база адресов и конфигурация, которыми выполняются операции роутеров.

    `owns_driver=True` — клиент закрывает драйвер в `aclose()`; иначе драйвер открывал
    не он, и закрывать его не ему (как `owns_handler` у `AsyncClient`).

    `session` — источник сессии (`SessionSource`): перед операцией клиент зовёт `ensure`,
    а операцию, упавшую отказом, который источник назвал истечением сессии, повторяет
    после `renew` — не больше `config.auth_retries` раз. `renew` вернул `None` — повторов
    нет, наружу уходит исходный отказ сайта. Про хранилище, контексты и локи клиент не
    знает ничего.
    """

    def __init__(
        self,
        driver: Driver,
        *,
        base_url: str = "",
        config: BrowserClientConfig | None = None,
        session: SessionSource[Any] | None = None,
        owns_driver: bool = False,
    ) -> None:
        self._driver = driver
        self._base_url = base_url
        self._config = BrowserClientConfig() if config is None else config
        self._session = session
        self._owns_driver = owns_driver
        self._closed = False

    @property
    def driver(self) -> Driver:
        return self._driver

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def config(self) -> BrowserClientConfig:
        return self._config

    @property
    def session(self) -> SessionSource[Any] | None:
        return self._session

    def without_session(self) -> AsyncBrowserClient:
        """Тот же драйвер, база и конфигурация, но без сессии — клиент для страницы входа."""
        return AsyncBrowserClient(self._driver, base_url=self._base_url, config=self._config)

    async def execute[TContent, TResult](
        self,
        operation: BrowserOperation[TContent, TResult],
        *,
        spec: _BrowserSpec[TContent] | None = None,
        options: BrowserCallOptions | None = None,
        api: AsyncBrowserApi | None = None,
    ) -> TResult:
        """Выполнить операцию на драйвере клиента.

        `spec` — спека с учётом роутера; без неё берётся объявление класса. `api` —
        роутер, из которого строятся состояния-исходы; роутер подставляет себя сам.
        """
        chosen = declaration_of(type(operation)) if spec is None else spec
        call = call_of(self._config, options, api, self._base_url)
        if self._session is None:
            return await run(operation, self._driver, chosen, call)
        return await self._run_in_session(self._session, operation, chosen, call)

    async def _run_in_session[TContent, TResult](
        self,
        session: SessionSource[Any],
        operation: BrowserOperation[TContent, TResult],
        spec: _BrowserSpec[TContent],
        call: Call,
    ) -> TResult:
        """`ensure` → операция → отказ-истечение → `renew` → повтор, `auth_retries` раз."""
        revision: object = await session.ensure(self)
        retries = self._config.auth_retries
        while True:
            try:
                return await run(operation, self._driver, spec, call)
            except Exception as error:
                if retries == 0 or not session.is_expired(error):
                    raise
                renewed = await session.renew(self, revision)
                if renewed is None:
                    raise
                retries -= 1
                revision = renewed

    async def aclose(self) -> None:
        """Закрыть драйвер, если он наш. Повторный вызов безвреден."""
        if self._closed:
            return
        self._closed = True
        closer = getattr(self._driver, "aclose", None) if self._owns_driver else None
        if closer is not None:
            await closer()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


__all__ = [
    "AsyncBrowserClient",
    "BrowserCallOptions",
    "BrowserClientConfig",
    "SessionSource",
    "call_of",
    "execute",
    "resolve_settle",
    "resolve_timeouts",
]
