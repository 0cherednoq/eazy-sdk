"""Коллекции и атрибуты: строки таблицы, пункты списка, значения полей.

Коллекция читается снимком и не ждёт — пустая таблица такой же ответ, как полная. Ждёт
действие над узлом по номеру, как действие над любым элементом. Узлы считаются среди
видимых и берутся у первого кандидата, у которого они есть.
"""

import asyncio
from dataclasses import dataclass
from typing import Annotated, NoReturn

import pytest
from eazy_sdk_browser import (
    AsyncBrowserApi,
    AsyncBrowserClient,
    Browser,
    BrowserCallOptions,
    BrowserOperation,
    Element,
    ElementNotFoundError,
    Elements,
    PageError,
    any_of,
    build_content,
    css,
    each,
    execute,
    outcomes,
    visible,
    when,
)
from eazy_sdk_browser.testing import FakeDriver, framed

from eazy_sdk import op

pytestmark = pytest.mark.unit

ROWS = "table.companies tr"
APP_FRAME = 'iframe[name="app"]'
FAST = BrowserCallOptions(timeout=0.05, element_timeout=0.05)


class NoCompaniesError(PageError):
    """Таблица компаний не появилась."""


class Table:
    rows: Annotated[Elements, each(css(ROWS))]


class Form:
    role: Annotated[Element, css('select[name="role"]')]
    agree: Annotated[Element, css('input[name="agree"]')]
    menu: Annotated[Element, css("a.menu")]
    name: Annotated[Element, css('input[name="name"]')]


async def _names(content: Table) -> list[str]:
    return await content.rows.texts()


async def _no_companies(content: Table) -> NoReturn:
    _ = content
    raise NoCompaniesError


@dataclass(frozen=True, slots=True, kw_only=True)
class ListCompanies(BrowserOperation[Table, list[str]]):
    __browser__ = Browser.goto(
        "/companies",
        Table,
        outcomes=outcomes(when(visible(css(ROWS)), then=_names), otherwise=_no_companies),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PickFirstInDialog(BrowserOperation[Table, None]):
    __browser__ = Browser.act(Table, scope=css("div.dialog"), frames=(APP_FRAME,))

    async def act(self, content: Table) -> None:
        await content.rows.nth(0).click()


class Portal(AsyncBrowserApi):
    list_companies = op(ListCompanies)


async def test_collection_is_every_node_of_the_first_candidate_that_has_them() -> None:
    """Строки старой и новой вёрстки в одну коллекцию не смешиваются."""
    driver = FakeDriver(counts={"table.new tr": 0, "table.old tr": 3, "table.other tr": 2})
    rows = Elements(driver, any_of("table.new tr", "table.old tr", "table.other tr"))

    assert await rows.count() == 3
    assert await rows.texts() == [f"текст table.old tr[{index}]" for index in range(3)]


async def test_empty_collection_is_an_answer_not_a_wait() -> None:
    driver = FakeDriver(latency=0.01)
    rows = Elements(driver, css(ROWS, timeout=5.0))
    loop = asyncio.get_running_loop()

    started = loop.time()
    count = await rows.count()

    assert count == 0
    assert loop.time() - started < 1.0


async def test_nth_acts_on_the_node_by_number_and_minus_one_is_the_last() -> None:
    driver = FakeDriver(counts={ROWS: 3})
    rows = Elements(driver, css(ROWS))

    await rows.nth(1).click()
    await rows.nth(-1).click()

    assert driver.log == [f"click {ROWS}[1]", f"click {ROWS}[2]"]


async def test_nth_waits_for_enough_nodes_and_names_the_number_when_they_never_come() -> None:
    driver = FakeDriver(counts={ROWS: 1})
    asyncio.get_running_loop().call_later(0.1, driver.counts.__setitem__, ROWS, 3)

    await Elements(driver, css(ROWS, timeout=1.0)).nth(2).click()
    with pytest.raises(ElementNotFoundError, match=r"\[5\]$") as raised:
        await Elements(driver, css(ROWS, timeout=0.05)).nth(5).click()

    assert driver.log == [f"click {ROWS}[2]"]
    assert raised.value.index == 5


async def test_walk_reads_an_attribute_of_every_node() -> None:
    driver = FakeDriver(
        counts={ROWS: 2},
        attributes={f"{ROWS}[0]": {"data-id": "c-1"}, f"{ROWS}[1]": {"data-id": "c-2"}},
    )
    rows = Elements(driver, css(ROWS))

    assert [await row.attribute("data-id") async for row in rows] == ["c-1", "c-2"]


async def test_collection_lives_inside_the_operation_scope_and_frames() -> None:
    inside = framed(f"div.dialog {ROWS}", (APP_FRAME,))
    driver = FakeDriver(counts={inside: 2, ROWS: 5})

    await execute(PickFirstInDialog(), driver)

    assert driver.log == [f"click {inside}[0]"]


async def test_element_selects_checks_hovers_and_reads_what_it_holds() -> None:
    driver = FakeDriver(
        present={'select[name="role"]', 'input[name="agree"]', "a.menu", 'input[name="name"]'},
        attributes={"a.menu": {"href": "/help"}},
    )
    form = build_content(Form, driver)

    await form.role.select("admin")
    await form.agree.check()
    await form.agree.uncheck()
    await form.menu.hover()
    await form.name.fill("ООО Ромашка")

    assert driver.log == [
        'select select[name="role"] = admin',
        'check input[name="agree"]',
        'uncheck input[name="agree"]',
        "hover a.menu",
        'fill input[name="name"] = ООО Ромашка',
    ]
    assert await form.role.value() == "admin"
    assert await form.name.value() == "ООО Ромашка"
    assert await form.menu.attribute("href") == "/help"
    assert await form.menu.attribute("title") is None


async def test_goto_reads_the_table_into_its_outcome() -> None:
    driver = FakeDriver(counts={ROWS: 2})
    portal = Portal(AsyncBrowserClient(driver, base_url="https://portal.example.com"))

    names = await portal.list_companies(options=FAST)

    assert names == [f"текст {ROWS}[0]", f"текст {ROWS}[1]"]


async def test_missing_table_is_the_declared_outcome_not_an_empty_list() -> None:
    driver = FakeDriver()
    portal = Portal(AsyncBrowserClient(driver, base_url="https://portal.example.com"))

    with pytest.raises(NoCompaniesError):
        await portal.list_companies(options=FAST)
