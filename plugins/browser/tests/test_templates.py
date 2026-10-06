"""Шаблоны селекторов: значение подставляется экранированным, а не как есть.

Кавычка в названии компании раньше ломала селектор, а то и меняла его смысл. Теперь
значение для CSS-селектора экранируется по `CSS.escape`, для текстовых движков playwright —
как строка в кавычках, а сам шаблон проверяется при объявлении.
"""

from typing import Annotated

import pytest
from eazy_sdk_browser import (
    BrowserDeclarationError,
    Template,
    build_content,
    css_escape,
    template,
    template_text,
    text_escape,
)
from eazy_sdk_browser.testing import FakeDriver

pytestmark = pytest.mark.unit


class Inbox:
    row: Annotated[Template, template('tr[data-name="{name}"]')]
    letter: Annotated[Template, template_text('text="{subject}"')]
    card: Annotated[Template, template("#{company_id}")]


@pytest.mark.parametrize(
    ("value", "escaped"),
    [
        pytest.param("ромашка", "ромашка", id="non-ascii-kept"),
        pytest.param('ООО "Ромашка"', r"ООО\ \"Ромашка\"", id="quote-and-space"),
        pytest.param("a\\b", r"a\\b", id="backslash"),
        pytest.param("42", r"\34 2", id="leading-digit"),
        pytest.param("-1", r"-\31 ", id="digit-after-leading-dash"),
        pytest.param("-", r"\-", id="lone-dash"),
        pytest.param("line\nbreak", r"line\a break", id="control"),
        pytest.param("\x00", "\ufffd", id="null"),
        pytest.param("a_b-c", "a_b-c", id="identifier-kept"),
    ],
)
def test_css_escape_follows_css_escape(value: str, escaped: str) -> None:
    assert css_escape(value) == escaped


def test_text_escape_keeps_the_text_and_escapes_the_string_delimiters() -> None:
    assert text_escape('ООО "Ромашка"') == r"ООО \"Ромашка\""
    assert text_escape("C:\\temp") == r"C:\\temp"
    assert text_escape("  Отчёт\n за  март ") == "Отчёт за март"


async def test_quote_in_the_value_does_not_break_the_css_selector() -> None:
    selector = r'tr[data-name="ООО\ \"Ромашка\""]'
    driver = FakeDriver(present={selector})

    await build_content(Inbox, driver).row(name='ООО "Ромашка"').click()

    assert driver.log == [f"click {selector}"]


async def test_text_template_escapes_for_the_text_engine() -> None:
    selector = r'text="Счёт \"№ 5\""'
    driver = FakeDriver(present={selector})

    await build_content(Inbox, driver).letter(subject='Счёт "№ 5"').click()

    assert driver.log == [f"click {selector}"]


async def test_identifier_value_is_escaped_too() -> None:
    selector = r"#\34 2"
    driver = FakeDriver(present={selector})

    await build_content(Inbox, driver).card(company_id=42).click()

    assert driver.log == [f"click {selector}"]


def test_template_values_are_exactly_its_placeholders() -> None:
    inbox = build_content(Inbox, FakeDriver())

    with pytest.raises(TypeError, match="takes subject, got topic"):
        inbox.letter(topic="Отчёт")
    with pytest.raises(TypeError, match="takes subject, got extra, subject"):
        inbox.letter(subject="Отчёт", extra="лишнее")


def test_template_placeholder_is_a_plain_name_checked_at_declaration() -> None:
    with pytest.raises(BrowserDeclarationError, match="plain field names"):
        template("tr[data-id={company_id!r}]")
    with pytest.raises(BrowserDeclarationError, match="plain field names"):
        template_text('text="{0}"')
