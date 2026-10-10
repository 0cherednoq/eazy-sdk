"""Фаза 59.8: подробности отказа читаются картой элементов, а не циклом в фабрике.

Сценарий — диалог почты «Невозможно использовать почтовые адреса»: каждая проблема — свой
элемент. Правило отказа объявляет карту диалога, фабрика получает её собранной.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import pytest
from eazy_sdk_browser import (
    AsyncBrowserApi,
    AsyncBrowserClient,
    BrowserOperation,
    Element,
    Failure,
    css,
    each,
    outcomes,
    text,
    visible,
    when,
)
from eazy_sdk_browser.errors import BrowserDeclarationError
from eazy_sdk_browser.failures import PageError
from eazy_sdk_browser.locators import Elements
from eazy_sdk_browser.spec import Browser
from eazy_sdk_browser.testing import FakeDriver

from eazy_sdk import op

CAPTION = "Невозможно использовать почтовые адреса"
PROBLEM = 'p[data-test-id^="error:"]'


class Composer:
    submit: Annotated[Element, css("button.send")]


class RejectedDialog:
    problems: Annotated[Elements, each(css(PROBLEM))]
    caption: Annotated[Element, css("h3.caption")]


class RecipientRejectedError(PageError):
    def __init__(self, problems: list[str], caption: str = "") -> None:
        self.problems = problems
        self.caption = caption
        super().__init__("; ".join(problems))


class DeniedError(PageError):
    """Нет прав."""


@dataclass(frozen=True, slots=True)
class Sent:
    pass


async def _sent(_content: Composer) -> Sent:
    return Sent()


async def _rejected(dialog: RejectedDialog) -> RecipientRejectedError:
    return RecipientRejectedError(await dialog.problems.texts(), await dialog.caption.text())


def _rejected_sync(dialog: RejectedDialog) -> RecipientRejectedError:
    return RecipientRejectedError([type(dialog.problems).__name__])


def _send(*rules: Failure) -> type[BrowserOperation[Composer, Sent]]:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Send(BrowserOperation[Composer, Sent]):
        __browser__ = Browser.act(
            Composer,
            errors=rules,
            outcomes=outcomes(when(visible(css("div.toast")), then=_sent), otherwise=_sent),
        )

        async def act(self, content: Composer) -> None:
            await content.submit.click()

    return Send


def _site(operation: type[BrowserOperation[Composer, Sent]], driver: FakeDriver) -> AsyncBrowserApi:
    class Mail(AsyncBrowserApi):
        send = op(operation)

    return Mail(AsyncBrowserClient(driver))


@pytest.mark.asyncio
async def test_the_factory_receives_the_built_map_and_reads_every_item() -> None:
    driver = FakeDriver(
        present={"button.send", "h3.caption"}, text=CAPTION, counts={PROBLEM: 2}
    )
    rule = Failure(when=text.contains(CAPTION), reads=RejectedDialog, exception=_rejected)

    with pytest.raises(RecipientRejectedError) as raised:
        await _site(_send(rule), driver).send()  # type: ignore[attr-defined]

    assert raised.value.problems == [f"текст {PROBLEM}[0]", f"текст {PROBLEM}[1]"]
    assert raised.value.caption == "текст h3.caption"


@pytest.mark.asyncio
async def test_an_empty_list_is_an_answer_and_a_plain_function_is_a_factory() -> None:
    driver = FakeDriver(present={"button.send"}, text=CAPTION)
    rule = Failure(when=text.contains(CAPTION), reads=RejectedDialog, exception=_rejected_sync)

    with pytest.raises(RecipientRejectedError) as raised:
        await _site(_send(rule), driver).send()  # type: ignore[attr-defined]

    assert raised.value.problems == ["Elements"]


@pytest.mark.asyncio
async def test_a_rule_that_does_not_hold_reads_nothing() -> None:
    driver = FakeDriver(present={"button.send", "div.toast"}, text="Письмо отправлено")
    rule = Failure(when=text.contains(CAPTION), reads=RejectedDialog, exception=_rejected)

    assert await _site(_send(rule), driver).send() == Sent()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_rule_without_a_map_works_as_before() -> None:
    driver = FakeDriver(present={"button.send"}, text="нет прав")

    with pytest.raises(DeniedError):
        await _site(_send(Failure(when=text.contains("нет прав"), exception=DeniedError)), driver).send()  # type: ignore[attr-defined]


def test_a_map_needs_a_factory_and_markers_on_every_field() -> None:
    class Unmarked:
        problems: Elements

    with pytest.raises(BrowserDeclarationError, match="карту читает фабрика"):
        Failure(when=text.contains(CAPTION), reads=RejectedDialog, exception=DeniedError)
    with pytest.raises(BrowserDeclarationError, match="аннотация без маркера"):
        Failure(when=text.contains(CAPTION), reads=Unmarked, exception=_rejected)
