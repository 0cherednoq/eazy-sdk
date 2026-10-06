"""Операция: спека, карта элементов, одно тело — `act` или переход — и раннер.

Форма объявления выбрана спайком (`docs/decisions.md`): карта — отдельный класс с
`Annotated`-маркерами, входы операции — поля frozen dataclass, драйвер в операцию не
попадает вовсе. Благодаря этому одну операцию можно гонять в нескольких вкладках разом.

Операция — значение: у неё нет метода `execute`, её выполняет роутер
(`portal.open_create_company()`) или функция `execute(operation, driver)` из
`eazy_sdk_browser.client`, как `client.send(request)` в `eazy-sdk`. Всё объявление
лежит в `__browser__`, и ошибки в нём видны при импорте, кодами D-B-xx, а не посреди
сценария.
"""

from __future__ import annotations

import types
from dataclasses import dataclass, fields
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Never,
    NoReturn,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from eazy_sdk_browser.conditions import Observation
from eazy_sdk_browser.content import Marker, Region, Timeouts, build_content
from eazy_sdk_browser.errors import BrowserDeclarationError, BrowserError
from eazy_sdk_browser.failures import enforce
from eazy_sdk_browser.interceptors import handle
from eazy_sdk_browser.locators import placeholders
from eazy_sdk_browser.navigation import navigation_mark, render_address
from eazy_sdk_browser.network import network_mark
from eazy_sdk_browser.profile import Capability, validate_profile
from eazy_sdk_browser.spec import _BrowserSpec

if TYPE_CHECKING:
    from eazy_sdk_browser.api import AsyncBrowserApi, _BrowserOperationDescriptor
    from eazy_sdk_browser.driver import Driver
    from eazy_sdk_browser.locators import Locator


class NotReadyError(BrowserError):
    """Страница не в том состоянии, которое операция объявила своим условием."""

    def __init__(self, locator: Locator) -> None:
        self.locator = locator
        super().__init__(locator.label)


@dataclass(frozen=True, slots=True)
class Call:
    """Контекст одного выполнения: таймауты, дедлайн исхода, роутер и база адресов.

    Собирается клиентом из конфигурации и опций вызова; роутер кладёт сюда себя, чтобы
    из него строились состояния-исходы.
    """

    timeouts: Timeouts
    within: float
    api: AsyncBrowserApi | None = None
    base_url: str = ""
    """К чему прикладывается относительный адрес `Browser.goto`."""


class BrowserOperation[TContent, TResult]:
    """Номинальная база операции. Полей и конструктора не несёт.

    `TContent` — карта, которую получит `act`; `TResult` — что вернёт выполнение:
    union исходов из `outcomes=` или `None`, если исходов нет.

    Единственный переопределяемый метод — `act`. Результат операции решает страница
    (объявленные исходы), а не тело: ни `if`, ни `try` про распознавание здесь нет.
    """

    __slots__ = ()
    __browser__: ClassVar[_BrowserSpec[Any]]

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        # Проверяется класс, который **сам** объявил спеку. База без `__browser__`
        # (общая для нескольких операций) — не ошибка: ошибкой она станет при
        # публикации, когда её попробуют выполнить (D-B-01).
        spec = cls.__dict__.get("__browser__")
        if isinstance(spec, _BrowserSpec):
            _check_declaration(cls, spec)

    async def act(self, content: TContent) -> None:
        """Действие, после которого страница и покажет один из исходов."""
        raise NotImplementedError

    @classmethod
    def __publish__(cls) -> _BrowserOperationDescriptor[..., TResult]:
        """Что `op()` ядра спрашивает у не-HTTP операции: опубликуй себя на роутере.

        Тип результата доезжает до вызывающего; сигнатура конструктора — нет: у метода
        класса нет доступа к `ParamSpec` конструктора, и `op(Operation)` ядра видит
        поля операции как `...`. Точная сигнатура — у `eazy_sdk_browser.api.publish`.
        """
        from eazy_sdk_browser.api import publish

        return publish(cls)


def declaration_of(operation: type[BrowserOperation[Any, Any]]) -> _BrowserSpec[Any]:
    """Спека класса операции — или отказ, если объявление собрано неверно."""
    spec = getattr(operation, "__browser__", None)
    if not isinstance(spec, _BrowserSpec):
        msg = f"D-B-01: {operation.__name__} has no __browser__; assign Browser.act(...)"
        raise BrowserDeclarationError(msg)
    params = getattr(operation, "__dataclass_params__", None)
    if params is None or not params.frozen:
        msg = (
            f"D-B-05: {operation.__name__} must be a frozen dataclass: "
            "an operation is a value, and the same value runs in several tabs at once"
        )
        raise BrowserDeclarationError(msg)
    if spec.navigation is not None:
        _check_address(operation, spec.navigation.url)
    return spec


async def run[TContent, TResult](
    operation: BrowserOperation[TContent, TResult],
    driver: Driver,
    spec: _BrowserSpec[TContent],
    call: Call,
) -> TResult:
    """Выполнить операцию на драйвере по уже разрешённой спеке.

    Требования к драйверу — первыми и все разом: названные в `requires=` и те, что нужны
    признакам объявления, правилам роутера тоже. Дальше у двух глаголов свой порядок.

    `Browser.act`: готовность, карта, помехи, действие. Помехи убираются **до**
    действия: клик сквозь баннер согласия уходит в баннер.

    `Browser.goto`: карта, переход, правила отказа, готовность, помехи. Готовность
    проверяется уже на открытой странице, а правила — раньше неё: увод на страницу
    входа виден сразу, а не через таймаут условия готовности.

    Водоразделы сети и переходов берутся прямо перед действием или переходом. Те же
    обработчики потом крутятся в ожидании исхода, и `once=True` считается на всю
    операцию — один набор `handled` на все фазы.

    `spec` — спека с учётом роутера (его правила, обработчики, фреймы); `call` —
    таймауты и дедлайн из клиента и опций вызова, роутер для состояний-исходов.
    """
    validate_profile(requirements_of(spec), driver.profile)
    navigation = spec.navigation
    if navigation is None:
        await _ensure_ready(spec, _observe(driver), call)
        content, page = _prepare(spec, driver, call)
        await handle(spec.handlers, page.next_round())
        await operation.act(content)
    else:
        address = render_address(navigation.url, operation, call.base_url)
        content, page = _prepare(spec, driver, call)
        await driver.goto(address, wait=navigation.wait, within=call.timeouts.navigation)
        await enforce(spec.errors, page.next_round())
        await _ensure_ready(spec, page, call)
        await handle(spec.handlers, page.next_round())
    if spec.outcomes is None:
        await enforce(spec.errors, page.next_round())
        return cast("TResult", None)
    result: TResult = await spec.outcomes.settle(content, page, spec.errors, spec.handlers, call)
    return result


def requirements_of(spec: _BrowserSpec[Any]) -> tuple[Capability, ...]:
    """Всё, что операции нужно от драйвера: названное в `requires=` и нужное признакам.

    Спека здесь уже с правилами роутера: их признаки видны только раннеру, а драйвер без
    нужной возможности отсекается до действия, а не посреди ожидания исхода.
    """
    return tuple(dict.fromkeys((*spec.requires, *_inferred(spec))))


def _prepare[TContent](
    spec: _BrowserSpec[TContent], driver: Driver, call: Call
) -> tuple[TContent, Observation]:
    """Карта и снимок — прямо перед тем, что операция сделает сама.

    Водоразделы берутся здесь же, и маркер ответа в карте запоминает свой при сборке:
    пришедшее раньше следствием действия или перехода быть не может. Операция без карты
    (`BrowserOperation[None, ...]`) получает `None`.
    """
    content: TContent
    if spec.content is type(None):
        content = None
    else:
        content = build_content(
            spec.content,
            driver,
            scope=() if spec.scope is None else (spec.scope.selectors[0],),
            frames=spec.frames,
            timeouts=call.timeouts,
        )
    return content, _observe(driver)


def _observe(driver: Driver) -> Observation:
    """Снимок с водоразделами на сейчас: ответы и переходы, случившиеся позже, — новые."""
    return Observation(driver, since=network_mark(driver), navigations=navigation_mark(driver))


async def _ensure_ready(spec: _BrowserSpec[Any], page: Observation, call: Call) -> None:
    """Условие готовности. Не выполнено — сначала слово правилам отказа.

    Кнопки нет, потому что портал увёл на вход: это истёкшая сессия, а не «страница не
    готова». `NotReadyError` остаётся на случай, который ни одно правило не объяснило.
    """
    if spec.at is None:
        return
    if await spec.at.timed(call.timeouts).find(page.driver) is not None:
        return
    await enforce(spec.errors, page.next_round())
    raise NotReadyError(spec.at)


# --- диагностики объявления ------------------------------------------------------------


def _check_declaration(operation: type[Any], spec: _BrowserSpec[Any]) -> None:
    """D-B-02, D-B-03, D-B-04, D-B-08, D-B-09 — то, что видно по одному объявлению."""
    if spec.navigation is not None and operation.act is not BrowserOperation.act:
        msg = (
            f"D-B-09: {operation.__name__} opens {spec.navigation.url!r} with Browser.goto "
            "and must not define act(): the navigation is its whole body, and an action "
            "on the opened page is a Browser.act operation"
        )
        raise BrowserDeclarationError(msg)
    declared = _class_arguments(operation)
    if declared is not None:
        content, result = declared
        if content is not spec.content:
            msg = (
                f"D-B-02: {operation.__name__} declares BrowserOperation[{_name(content)}, ...] "
                f"but Browser.act({_name(spec.content)}, ...)"
            )
            raise BrowserDeclarationError(msg)
        _check_result(operation, spec, result)
    _check_requirements(operation, spec, _markers_of(spec.content, owner=operation))


_INFERRED = (Capability.network, Capability.navigation_events, Capability.rich_text)
"""Возможности, которые видны по самому объявлению — по маркерам карты и признакам."""


def _check_requirements(
    operation: type[Any], spec: _BrowserSpec[Any], markers: list[object]
) -> None:
    """D-B-04: объявленные возможности против тех, что нужны карте и признакам операции.

    Сверяются только сеть, переходы и редактор: их видно по объявлению — признак или
    маркер карты сам называет свою возможность (`requires`). `session_state` или
    `page_requests` так не выразить — там `requires=` остаётся словом автора.
    """
    used = set(_inferred(spec))
    for marker in markers:
        used.update(getattr(marker, "requires", ()))
    for capability in _INFERRED:
        declared = capability in spec.requires
        if capability in used and not declared:
            msg = (
                f"D-B-04: {operation.__name__} needs Capability.{capability.name} for its "
                f"map or signs but does not declare requires=(Capability.{capability.name},)"
            )
            raise BrowserDeclarationError(msg)
        if declared and capability not in used:
            msg = (
                f"D-B-04: {operation.__name__} declares Capability.{capability.name} "
                "but nothing in its map or signs uses it"
            )
            raise BrowserDeclarationError(msg)


def _inferred(spec: _BrowserSpec[Any]) -> tuple[Capability, ...]:
    """Возможности, которые требуют признаки объявления: исходов, правил, помех."""
    signs = [rule.when for rule in spec.errors]
    signs += [handler.when for handler in spec.handlers]
    if spec.outcomes is not None:
        signs += [case.sign for case in spec.outcomes.cases]
    return tuple(dict.fromkeys(capability for sign in signs for capability in sign.requires))


def _check_address(operation: type[Any], template: str) -> None:
    """D-B-10: каждое `{имя}` в адресе перехода — поле операции."""
    try:
        names = placeholders(template)
    except ValueError as error:
        msg = f"D-B-10: {operation.__name__} navigates to {template!r}: {error}"
        raise BrowserDeclarationError(msg) from error
    known = {item.name for item in fields(operation)}
    missing = [name for name in names if name not in known]
    if missing:
        msg = (
            f"D-B-10: {operation.__name__} navigates to {template!r} "
            f"but has no field {', '.join(missing)}"
        )
        raise BrowserDeclarationError(msg)


def _check_result(operation: type[Any], spec: _BrowserSpec[Any], result: object) -> None:
    """D-B-08: union исходов из объявления против `TResult` класса."""
    if spec.outcomes is None:
        if result is not type(None):
            msg = (
                f"D-B-08: {operation.__name__} declares a result of {_name(result)} "
                "but no outcomes=; an operation without outcomes returns None"
            )
            raise BrowserDeclarationError(msg)
        return
    members: list[object] = []
    for case in spec.outcomes.cases:
        member = case.to if case.to is not None else _return_of(case.then)
        if member is _UNKNOWN:
            return
        members.append(member)
    tail = _return_of(spec.outcomes.otherwise)
    if tail is _UNKNOWN:
        return
    if tail is not _NEVER:
        members.append(tail)
    declared = set(_members(result))
    if declared != set(members):
        expected = " | ".join(_name(member) for member in members)
        msg = (
            f"D-B-08: {operation.__name__} declares a result of {_name(result)} "
            f"but its outcomes build {expected}"
        )
        raise BrowserDeclarationError(msg)


def _markers_of(content: type[Any], *, owner: type[Any]) -> list[object]:
    """Маркеры карты и её подкарт. Поле без маркера — D-B-03, при импорте."""
    try:
        hints = get_type_hints(content, include_extras=True)
    except NameError:
        # Отложенная аннотация на имя, которого ещё нет: проверит сборка карты.
        return []
    found: list[object] = []
    for name, annotation in hints.items():
        markers = [argument for argument in get_args(annotation) if isinstance(argument, Marker)]
        if not markers:
            msg = (
                f"D-B-03: {owner.__name__}: {content.__qualname__}.{name} "
                "is annotated without a marker"
            )
            raise BrowserDeclarationError(msg)
        marker = markers[0]
        found.append(marker)
        if isinstance(marker, Region):
            found.extend(_markers_of(marker.card, owner=owner))
    return found


def _class_arguments(operation: type[Any]) -> tuple[object, object] | None:
    """`(TContent, TResult)` из `class Op(BrowserOperation[Content, Result])`.

    `None`, если параметры не заданы или остались переменными типа: тогда сравнивать
    не с чем, и проверка ждёт конкретного потомка.
    """
    for cls in operation.__mro__:
        for base in getattr(cls, "__orig_bases__", ()):
            origin = get_origin(base)
            if not (isinstance(origin, type) and issubclass(origin, BrowserOperation)):
                continue
            arguments = get_args(base)
            if len(arguments) != 2 or any(_is_type_variable(arg) for arg in arguments):
                return None
            return cast("tuple[object, object]", arguments)
    return None


_UNKNOWN = object()
_NEVER = object()


def _return_of(function: object) -> object:
    """Что строит фабрика исхода по её аннотации; `_UNKNOWN`, если аннотации нет."""
    if function is None:
        return _UNKNOWN
    try:
        hints = get_type_hints(function)
    except (NameError, TypeError):
        return _UNKNOWN
    declared = hints.get("return", _UNKNOWN)
    if declared is _UNKNOWN:
        return _UNKNOWN
    if declared is NoReturn or declared is Never:
        return _NEVER
    return declared


def _members(annotation: object) -> tuple[object, ...]:
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        return get_args(annotation)
    return (annotation,)


def _is_type_variable(argument: object) -> bool:
    return type(argument).__name__ in {"TypeVar", "ParamSpec", "TypeVarTuple"}


def _name(annotation: object) -> str:
    if isinstance(annotation, type):
        return annotation.__name__
    return repr(annotation)


__all__ = [
    "BrowserOperation",
    "Call",
    "NotReadyError",
    "declaration_of",
    "requirements_of",
    "run",
]
