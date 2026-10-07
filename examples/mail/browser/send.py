"""Submit the mail composer and distinguish its three visible outcomes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import op
from eazy_sdk_browser import (
    AsyncBrowserApi,
    Browser,
    BrowserCallOptions,
    BrowserOperation,
    Element,
    css,
    outcomes,
    visible,
    when,
)

from examples.mail.site._playwright import playwright_mail

OPTIONS = BrowserCallOptions(timeout=1.0, element_timeout=5.0)


# region docs: browser-send-operation
# examples/mail/browser/send.py
class ComposerPage:
    recipient: Annotated[Element, css('input[name="recipient"]')]
    subject: Annotated[Element, css('input[name="subject"]')]
    body: Annotated[Element, css('textarea[name="body"]')]
    submit: Annotated[Element, css('button[type="submit"]')]
    rejected: Annotated[Element, css('[data-outcome="rejected"]')]


@dataclass(frozen=True, slots=True)
class Sent:
    pass


@dataclass(frozen=True, slots=True)
class Rejected:
    pass


@dataclass(frozen=True, slots=True)
class Silent:
    pass


async def _sent(content: ComposerPage) -> Sent:
    _ = content
    return Sent()


async def _rejected(content: ComposerPage) -> Rejected:
    _ = content
    return Rejected()


async def _silent(content: ComposerPage) -> Silent:
    _ = content
    return Silent()


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenComposer(BrowserOperation[None, None]):
    __browser__ = Browser.goto("/compose/", at=css("[data-composer]"))


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitMessage(BrowserOperation[ComposerPage, Sent | Rejected | Silent]):
    __browser__ = Browser.act(
        ComposerPage,
        at=css("[data-composer]"),
        outcomes=outcomes(
            when(visible(css('[role="status"]')), then=_sent),
            when(visible(css('[data-outcome="rejected"]')), then=_rejected),
            otherwise=_silent,
        ),
    )

    recipient: str
    subject: str
    body: str

    async def act(self, content: ComposerPage) -> None:
        if await content.rejected.visible():
            await content.rejected.press("Escape")
        await content.recipient.fill(self.recipient)
        await content.subject.fill(self.subject)
        await content.body.fill(self.body)
        await content.submit.click()


class ComposerApi(AsyncBrowserApi):
    open = op(OpenComposer)
    send = op(SubmitMessage)
# endregion docs: browser-send-operation


# region docs: browser-send-flow
# examples/mail/browser/send.py
async def send_three_messages() -> tuple[
    Sent | Rejected | Silent,
    Sent | Rejected | Silent,
    Sent | Rejected | Silent,
]:
    async with playwright_mail() as runtime:
        composer = ComposerApi(runtime.client)
        await composer.open(options=OPTIONS)
        sent = await composer.send(
            recipient="grace@mail.example",
            subject="Проверка доставки",
            body="Встречаемся в пятницу.",
            options=OPTIONS,
        )
        rejected = await composer.send(
            recipient="rejected@mail.example",
            subject="Проверка адреса",
            body="Это письмо отклонят.",
            options=OPTIONS,
        )
        silent = await composer.send(
            recipient="quiet@mail.example",
            subject="Тихая доставка",
            body="Проверим папку отправленных.",
            options=OPTIONS,
        )
        return sent, rejected, silent
# endregion docs: browser-send-flow


async def run() -> None:
    sent, rejected, silent = await send_three_messages()
    print(f"sent: {type(sent).__name__}")
    print(f"rejected: {type(rejected).__name__}")
    print(f"silent: {type(silent).__name__}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
