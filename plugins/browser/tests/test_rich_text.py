"""Редактор форматированного текста — возможность драйвера, а не минимум элемента.

Положить разметку в `contenteditable` умеет не каждый драйвер, поэтому `set_html` ушёл из
протокола `Element` в `Capability.rich_text`: поле карты `rich(...)`, объявленная
возможность у операции, отказ драйверу без неё — до первого действия.
"""

from dataclasses import dataclass
from typing import Annotated

import pytest
from eazy_sdk_browser import (
    Browser,
    BrowserDeclarationError,
    BrowserOperation,
    Capability,
    Element,
    ElementNotFoundError,
    RichText,
    build_content,
    css,
    execute,
    rich,
)
from eazy_sdk_browser.testing import FakeDriver, RichTextFakeDriver, RichTextLiar

from eazy_sdk.handlers import CapabilityMismatchError

pytestmark = pytest.mark.unit

SUBJECT = 'input[name="subject"]'
EDITOR = 'div[contenteditable="true"]'


class Letter:
    subject: Annotated[Element, css(SUBJECT)]
    body: Annotated[RichText, rich(css(EDITOR))]


class Plain:
    subject: Annotated[Element, css(SUBJECT)]


@dataclass(frozen=True, slots=True, kw_only=True)
class Write(BrowserOperation[Letter, None]):
    __browser__ = Browser.act(Letter, requires=(Capability.rich_text,))

    html: str

    async def act(self, content: Letter) -> None:
        await content.subject.fill("Тема")
        await content.body.set_html(self.html)


async def test_markup_goes_into_the_editor_through_the_driver() -> None:
    driver = RichTextFakeDriver(present={SUBJECT, EDITOR})

    await execute(Write(html="<p><b>Жирный</b></p>"), driver)

    assert driver.log == [f"fill {SUBJECT} = Тема", f"html {EDITOR} = <p><b>Жирный</b></p>"]


async def test_driver_without_rich_text_is_refused_before_acting() -> None:
    driver = FakeDriver(present={SUBJECT, EDITOR})

    with pytest.raises(CapabilityMismatchError) as raised:
        await execute(Write(html="<p>Текст</p>"), driver)

    assert raised.value.dimensions == ("rich_text",)
    assert driver.log == []


def test_map_with_an_editor_is_refused_when_built_for_a_driver_without_it() -> None:
    with pytest.raises(CapabilityMismatchError):
        build_content(Letter, FakeDriver())


def test_profile_that_promises_rich_text_without_the_method_is_an_adapter_error() -> None:
    with pytest.raises(TypeError, match="set_html"):
        build_content(Letter, RichTextLiar())


async def test_missing_editor_is_not_found_like_any_element() -> None:
    body = build_content(Letter, RichTextFakeDriver()).body

    with pytest.raises(ElementNotFoundError):
        await body.set_html("<p>Текст</p>")


async def test_editor_is_still_an_element() -> None:
    driver = RichTextFakeDriver(present={EDITOR})
    body = build_content(Letter, driver).body

    await body.click()

    assert await body.text() == f"текст {EDITOR}"
    assert driver.log == [f"click {EDITOR}"]


def test_plain_element_protocol_has_no_markup() -> None:
    assert not hasattr(Element, "set_html")


def test_d_b_04_editor_in_the_map_and_its_requirement_go_together() -> None:
    with pytest.raises(
        BrowserDeclarationError, match=r"D-B-04: Undeclared needs Capability\.rich_text"
    ):

        class Undeclared(BrowserOperation[Letter, None]):
            __browser__ = Browser.act(Letter)

    with pytest.raises(
        BrowserDeclarationError, match=r"D-B-04: Stale declares Capability\.rich_text"
    ):

        class Stale(BrowserOperation[Plain, None]):
            __browser__ = Browser.act(Plain, requires=(Capability.rich_text,))
