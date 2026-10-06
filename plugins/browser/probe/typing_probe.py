"""Пробник типизатора: что библиотека показывает `basedpyright`, а что прячет.

Модуль намеренно содержит ошибки и лежит вне `include` basedpyright, поэтому в
`poe check` не участвует. Запуск: `uv run poe probe`. Важен не факт падения, а
какие строки типизатор назовёт, а какие пропустит.
"""

from __future__ import annotations

from typing import Annotated, assert_never, reveal_type

from eazy_sdk_browser import Browser, css, navigated, outcomes, url, visible, when
from eazy_sdk_browser import Elements, each
from examples.browser_portal import (
    CompaniesPage,
    CompaniesPortal,
    CompanyExistsError,
    CreateCompanyForm,
    Created,
    PortalBrokenError,
    SessionExpiredError,
)


async def probe_operation_result_is_typed(portal: CompaniesPortal) -> None:
    """Отказ по умолчанию (`NoReturn`) не добавляет члена в union исходов."""
    dialog = await portal.open_create_company()
    reveal_type(dialog)  # ожидаем CreateCompanyDialog, без | None
    reveal_type(portal.submit_company.request(name="x", inn="y"))  # ожидаем BrowserOperation[Any, Created]
    # Поля операции через `op()` ядра типизатор не видит (`__publish__` — метод класса,
    # без доступа к ParamSpec конструктора): `name=1` здесь **не** ошибка. Долг ядра, B6.2.
    reveal_type(await dialog.submit(name="ООО Ромашка", inn="7701234567"))  # ожидаем Created


def probe_form_is_not_a_field_of_the_page(page: CompaniesPage) -> None:
    """Карта списка про форму не знает: поля формы там просто нет."""
    reveal_type(page.create_button)  # ожидаем Element
    reveal_type(page.filters.query)  # ожидаем Element: подкарта типизирована
    reveal_type(page.name)  # обязана быть ошибка: имя компании вводится не здесь


async def probe_api_reply_is_typed(form: CreateCompanyForm) -> None:
    """Ответ портала разобран объявлением: модель успеха, а не словарь.

    Отказы (409, 403, 5xx) сюда не доходят — объявление поднимает их исключением,
    поэтому в типе нет ни `| None`, ни поля `error`.
    """
    reply = await form.reply.value()
    reveal_type(reply)  # ожидаем CreateReply
    reveal_type(reply.id)  # ожидаем str, без | None
    reveal_type(reply.error)  # обязана быть ошибка: отказ сюда не приходит


def probe_declared_failures_are_distinguishable(error: Exception) -> str:
    """Отказы названы, поэтому вызывающий разбирает их типом, а не строкой."""
    match error:
        case CompanyExistsError():
            return "ИНН занят"
        case SessionExpiredError():
            return "перелогиниться"
        case PortalBrokenError():
            return "повторить позже"
        case _:
            return "неизвестный отказ"


def probe_exhaustive_match_is_checked(outcome: Created | CompanyExistsError) -> str:
    """Забытая ветка обязана быть ошибкой: `CompanyExistsError` здесь не разобран."""
    match outcome:
        case Created():
            return "создано"
        case _ as unreachable:
            assert_never(unreachable)


class _A:
    pass


class _B:
    pass


class _C:
    pass


class _D:
    pass


class _E:
    pass


async def _a(page: CompaniesPage) -> _A:
    _ = page
    return _A()


async def _b(page: CompaniesPage) -> _B:
    _ = page
    return _B()


async def _c(page: CompaniesPage) -> _C:
    _ = page
    return _C()


async def _d(page: CompaniesPage) -> _D:
    _ = page
    return _D()


async def _e(page: CompaniesPage) -> _E:
    _ = page
    return _E()


def probe_outcomes_union_is_inferred_up_to_six_cases() -> None:
    """Четыре и пять случаев типизированы точно: union из декларации, не `Any`."""
    four = outcomes(
        when(visible(css("a")), then=_a),
        when(visible(css("b")), then=_b),
        when(visible(css("c")), then=_c),
        otherwise=_d,
    )
    five = outcomes(
        when(visible(css("a")), then=_a),
        when(visible(css("b")), then=_b),
        when(visible(css("c")), then=_c),
        when(visible(css("d")), then=_d),
        otherwise=_e,
    )
    reveal_type(four)  # ожидаем Outcomes[CompaniesPage, _A | _B | _C | _D]
    reveal_type(five)  # ожидаем Outcomes[CompaniesPage, _A | _B | _C | _D | _E]


def probe_navigation_is_an_operation_without_a_map() -> None:
    """Переход без карты — `_BrowserSpec[None]`; с картой — её тип, как у `act`."""
    reveal_type(Browser.goto("/companies"))  # ожидаем _BrowserSpec[None]
    reveal_type(Browser.goto("/companies", CompaniesPage))  # ожидаем _BrowserSpec[CompaniesPage]
    reveal_type(navigated() & url.contains("/inbox"))  # ожидаем Sign


class _Table:
    # Поля карты заполняет сборщик, а не `__init__`: это не ошибка пробы.
    rows: Annotated[Elements, each(css("tr"))]  # pyright: ignore[reportUninitializedInstanceVariable]


async def probe_collection_is_typed(table: _Table) -> None:
    """Коллекция отдаёт элементы и строки, а не `Any`."""
    reveal_type(await table.rows.texts())  # ожидаем list[str]
    reveal_type(table.rows.nth(-1))  # ожидаем Lazy
    async for row in table.rows:
        reveal_type(row)  # ожидаем Element
        reveal_type(await row.attribute("data-id"))  # ожидаем str | None
