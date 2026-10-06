"""Объявление операции проверяется при импорте: коды D-B-xx — `PlanError` ядра.

Ошибка в объявлении — ошибка автора SDK, и видна она должна быть там, где он её
сделал: при импорте модуля, а не посреди сценария на живой странице.
"""

from dataclasses import dataclass
from typing import Annotated, NoReturn

import pytest
from eazy_sdk_browser import (
    Browser,
    BrowserDeclarationError,
    BrowserOperation,
    Capability,
    Element,
    css,
    execute,
    outcomes,
    region,
    visible,
    when,
)
from eazy_sdk_browser.network import ApiResponse, ApiValue, json_as
from eazy_sdk_browser.testing import FakeDriver

from eazy_sdk.core.errors import PlanError

pytestmark = pytest.mark.unit


class Page:
    button: Annotated[Element, css("button")]


class ApiPage:
    reply: Annotated[ApiValue[dict[str, object]], ApiResponse("/api", json_as(dict))]


@dataclass(frozen=True, slots=True)
class Opened:
    pass


@dataclass(frozen=True, slots=True)
class Closed:
    pass


async def _opened(content: Page) -> Opened:
    _ = content
    return Opened()


async def _closed(content: Page) -> Closed:
    _ = content
    return Closed()


async def _missing(content: Page) -> NoReturn:
    _ = content
    raise AssertionError


def test_d_b_01_operation_without_spec_is_refused_when_executed() -> None:
    """База без `__browser__` допустима, пока её не пробуют выполнить."""

    @dataclass(frozen=True, slots=True)
    class Bare(BrowserOperation[Page, None]):
        async def act(self, content: Page) -> None:
            _ = content

    with pytest.raises(BrowserDeclarationError, match="D-B-01"):
        import asyncio

        asyncio.run(execute(Bare(), FakeDriver()))


def test_d_b_02_content_of_spec_must_match_the_class_parameter() -> None:
    with pytest.raises(BrowserDeclarationError, match="D-B-02") as raised:

        class Mismatch(BrowserOperation[Page, None]):
            __browser__ = Browser.act(ApiPage, requires=(Capability.network,))

    assert isinstance(raised.value, PlanError)


def test_d_b_03_map_field_without_marker_is_refused_at_import() -> None:
    class Broken:
        button: Element

    with pytest.raises(BrowserDeclarationError, match=r"D-B-03: .*Broken\.button"):

        class Op(BrowserOperation[Broken, None]):
            __browser__ = Browser.act(Broken)


def test_d_b_03_looks_inside_regions() -> None:
    class Panel:
        query: Element

    class Outer:
        filters: Annotated[Panel, region(Panel, root=css("form"))]

    with pytest.raises(BrowserDeclarationError, match=r"D-B-03: .*Panel\.query"):

        class Op(BrowserOperation[Outer, None]):
            __browser__ = Browser.act(Outer)


def test_d_b_04_api_response_in_map_requires_network() -> None:
    with pytest.raises(BrowserDeclarationError, match="D-B-04"):

        class Reads(BrowserOperation[ApiPage, None]):
            __browser__ = Browser.act(ApiPage)


def test_d_b_04_network_without_api_response_is_a_stale_requirement() -> None:
    with pytest.raises(BrowserDeclarationError, match="D-B-04"):

        class Claims(BrowserOperation[Page, None]):
            __browser__ = Browser.act(Page, requires=(Capability.network,))


def test_d_b_05_operation_must_be_a_frozen_dataclass() -> None:
    @dataclass
    class Mutable(BrowserOperation[Page, None]):
        __browser__ = Browser.act(Page)

        async def act(self, content: Page) -> None:
            _ = content

    with pytest.raises(BrowserDeclarationError, match="D-B-05"):
        import asyncio

        asyncio.run(execute(Mutable(), FakeDriver()))


def test_d_b_08_result_parameter_must_be_the_union_of_outcomes() -> None:
    """`TResult` класса и union исходов расходиться не могут — типизатор их не связывает."""
    with pytest.raises(BrowserDeclarationError, match="D-B-08"):

        class Narrow(BrowserOperation[Page, Opened]):
            __browser__ = Browser.act(
                Page,
                outcomes=outcomes(
                    when(visible(css("a")), then=_opened),
                    when(visible(css("b")), then=_closed),
                    otherwise=_missing,
                ),
            )

    class Exact(BrowserOperation[Page, Opened | Closed]):
        __browser__ = Browser.act(
            Page,
            outcomes=outcomes(
                when(visible(css("a")), then=_opened),
                when(visible(css("b")), then=_closed),
                otherwise=_missing,
            ),
        )

    assert Exact.__browser__.outcomes is not None


def test_d_b_08_operation_without_outcomes_returns_none() -> None:
    with pytest.raises(BrowserDeclarationError, match="D-B-08"):

        class Claims(BrowserOperation[Page, Opened]):
            __browser__ = Browser.act(Page)


def test_generic_base_without_arguments_is_left_to_its_subclasses() -> None:
    """Общая база `Portal[TContent, TResult]` не сравнивается: параметры ещё не известны."""

    class PortalBase[TContent, TResult](BrowserOperation[TContent, TResult]):
        pass

    @dataclass(frozen=True, slots=True)
    class Concrete(PortalBase[Page, Opened]):
        __browser__ = Browser.act(
            Page, outcomes=outcomes(when(visible(css("a")), then=_opened), otherwise=_missing)
        )

        async def act(self, content: Page) -> None:
            _ = content

    assert Concrete.__browser__.content is Page
