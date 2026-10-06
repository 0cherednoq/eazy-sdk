"""Роутер браузерных операций: `portal.open_create_company()` вместо `execute(...)` руками.

Та же форма, что `AsyncApi` в `eazy-sdk`: класс перечисляет операции через `op()`,
сервисные атрибуты объявляются один раз на роутер (или на общую базу в его MRO) и
достаются каждой операции:

    class CompaniesPortal(AsyncBrowserApi):
        errors = PORTAL_ERRORS
        handlers = (COOKIE_BANNER,)
        open_create_company = op(OpenCreateCompany)
        submit_company = op(SubmitCompany)

`op()` — тот же `op` ядра: браузерная операция публикует себя сама (`__publish__`),
и HTTP-сторона о плагине не знает. Правила отказа операции стоят выше правил роутера
(`inherit_errors=False` отключает наследование), обработчики помех складываются,
фреймы роутера идут снаружи фреймов операции.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast, overload

from eazy_sdk.api import _OperationDescriptor
from eazy_sdk_browser.errors import BrowserDeclarationError
from eazy_sdk_browser.operations import BrowserOperation, declaration_of

if TYPE_CHECKING:
    from collections.abc import Callable

    from eazy_sdk_browser.client import AsyncBrowserClient, BrowserCallOptions
    from eazy_sdk_browser.failures import FailureRules
    from eazy_sdk_browser.interceptors import Handlers
    from eazy_sdk_browser.spec import _BrowserSpec


@dataclass(frozen=True, slots=True)
class BrowserServiceDefaults:
    """Сервисное объявление роутера: то, что достаётся каждой его операции."""

    errors: FailureRules = ()
    handlers: Handlers = ()
    frames: tuple[str, ...] = ()


SERVICE_ATTRIBUTES = ("errors", "handlers", "frames")
"""Атрибуты класса, которые роутер (или база в его MRO) может объявить."""


def resolve_spec(spec: _BrowserSpec[Any], defaults: BrowserServiceDefaults) -> _BrowserSpec[Any]:
    """Спека операции с учётом объявления роутера.

    Порядок правил отказа: операция (precedence 0) выше роутера (1) — частные правила
    раньше общих, как в ядре. Обработчики — операции, потом роутера. Фреймы роутера
    снаружи: почтовый интерфейс целиком сидит во фрейме, и объявлять его у каждой
    операции — переписывать одно и то же.
    """
    errors = spec.errors + defaults.errors if spec.inherit_errors else spec.errors
    return replace(
        spec,
        errors=errors,
        handlers=spec.handlers + defaults.handlers,
        frames=defaults.frames + spec.frames,
    )


class _BoundBrowserOperation[**P, TResult]:
    """Операция, привязанная к роутеру: вызов, запрос-значение, отправка значения."""

    def __init__(
        self, descriptor: _BrowserOperationDescriptor[P, TResult], api: AsyncBrowserApi
    ) -> None:
        self._descriptor = descriptor
        self._api = api
        self.__name__ = descriptor.__name__
        self.__doc__ = descriptor.__doc__
        self.__signature__ = descriptor.signature

    @property
    def Operation(self) -> type[BrowserOperation[Any, TResult]]:  # noqa: N802 — имя класса, как у ядра
        """Класс операции — для `isinstance` и построения запроса вручную."""
        return self._descriptor.operation

    def request(self, *args: P.args, **kwargs: P.kwargs) -> BrowserOperation[Any, TResult]:
        """Операция как значение: собрать, не выполняя."""
        return self._descriptor.operation(*args, **kwargs)

    async def send(
        self,
        request: BrowserOperation[Any, TResult],
        *,
        options: BrowserCallOptions | None = None,
    ) -> TResult:
        """Выполнить собранную операцию с объявлением роутера."""
        return await _dispatch(self._api, request, self._descriptor, options)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> TResult:
        options = cast("BrowserCallOptions | None", kwargs.pop("options", None))
        return await self.send(self.request(*args, **kwargs), options=options)


class _BrowserOperationDescriptor[**P, TResult]:
    """Член роутера: класс операции и её спека, проверенные при публикации."""

    def __init__(self, operation: type[BrowserOperation[Any, TResult]]) -> None:
        self.operation = operation
        self.spec = declaration_of(operation)
        self.signature = inspect.signature(operation)
        self.__name__ = operation.__name__
        self.__qualname__ = operation.__qualname__
        self.__doc__ = operation.__doc__
        self.__signature__ = self.signature

    def __set_name__(self, owner: type[object], name: str) -> None:
        """D-B-06: браузерная операция принадлежит `AsyncBrowserApi` и больше никому."""
        if not (isinstance(owner, type) and issubclass(owner, AsyncBrowserApi)):
            msg = (
                f"D-B-06: browser operation {self.__name__} is published on "
                f"{owner.__name__} as {name!r}; browser operations belong to AsyncBrowserApi"
            )
            raise BrowserDeclarationError(msg)

    @overload
    def __get__(self, instance: None, owner: type[object]) -> Self: ...

    @overload
    def __get__(
        self, instance: object, owner: type[object] | None = None
    ) -> _BoundBrowserOperation[P, TResult]: ...

    def __get__(self, instance: object | None, owner: type[object] | None = None) -> object:
        if instance is None:
            return self
        if not isinstance(instance, AsyncBrowserApi):
            msg = "browser operation must be bound to AsyncBrowserApi"
            raise TypeError(msg)
        return _BoundBrowserOperation(self, instance)


def publish[**P, TResult](
    operation: Callable[P, BrowserOperation[Any, TResult]],
) -> _BrowserOperationDescriptor[P, TResult]:
    """Дескриптор для класса операции. `op(Operation)` ядра приходит сюда через `__publish__`."""
    return _BrowserOperationDescriptor(cast("type[BrowserOperation[Any, TResult]]", operation))


class AsyncBrowserApi:
    """Роутер: клиент плюс сервисное объявление, собранное по MRO.

    Состояния-исходы (`when(..., to=State)`) строятся из роутера: состояние держит его
    и вызывает следующие операции через него, а не через драйвер.
    """

    errors: ClassVar[FailureRules] = ()
    handlers: ClassVar[Handlers] = ()
    frames: ClassVar[tuple[str, ...]] = ()
    _service_defaults: ClassVar[BrowserServiceDefaults] = BrowserServiceDefaults()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        for name in dir(cls):
            member = inspect.getattr_static(cls, name)
            # D-B-07: у HTTP-операции есть адрес и статус, у браузерного роутера —
            # страница; на нём такой операции не место.
            if isinstance(member, _OperationDescriptor):
                msg = (
                    f"D-B-07: HTTP operation {name} is published on {cls.__name__}; "
                    "an AsyncBrowserApi carries browser operations"
                )
                raise BrowserDeclarationError(msg)
        cls._service_defaults = BrowserServiceDefaults(
            errors=tuple(cls.errors), handlers=tuple(cls.handlers), frames=tuple(cls.frames)
        )

    def __init__(
        self, client: AsyncBrowserClient, *, defaults: BrowserServiceDefaults | None = None
    ) -> None:
        self._client = client
        self._defaults = self._service_defaults if defaults is None else defaults

    @classmethod
    def __compose__(cls, client: object) -> Self:
        """Как `AsyncRoot` строит этот роутер: клиент приходит из `bind(..., client=)`."""
        from eazy_sdk_browser.client import AsyncBrowserClient

        if not isinstance(client, AsyncBrowserClient):
            msg = (
                f"{cls.__name__} runs on AsyncBrowserClient; bind it in the root with "
                f"bind({cls.__name__}, client=AsyncBrowserClient(driver))"
            )
            raise TypeError(msg)
        return cls(client)

    @property
    def client(self) -> AsyncBrowserClient:
        return self._client

    @property
    def defaults(self) -> BrowserServiceDefaults:
        return self._defaults


async def _dispatch[TResult](
    api: AsyncBrowserApi,
    request: BrowserOperation[Any, TResult],
    descriptor: _BrowserOperationDescriptor[Any, TResult],
    options: BrowserCallOptions | None,
) -> TResult:
    if not isinstance(request, descriptor.operation):
        msg = f"send() expects {descriptor.operation.__name__}, got {type(request).__name__}"
        raise TypeError(msg)
    spec = resolve_spec(descriptor.spec, api.defaults)
    return await api.client.execute(request, spec=spec, options=options, api=api)


__all__ = [
    "SERVICE_ATTRIBUTES",
    "AsyncBrowserApi",
    "BrowserServiceDefaults",
    "publish",
    "resolve_spec",
]
