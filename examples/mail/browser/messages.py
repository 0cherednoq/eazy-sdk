"""Read the initial inbox collection and load the next batch in Chromium."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, NoReturn

from eazy_sdk import op
from eazy_sdk_browser import (
    AsyncBrowserApi,
    Browser,
    BrowserCallOptions,
    BrowserOperation,
    Element,
    Elements,
    css,
    each,
    outcomes,
    visible,
    when,
)

from examples.mail.site._playwright import playwright_mail

OPTIONS = BrowserCallOptions(timeout=5.0, element_timeout=5.0)
MESSAGE = '[data-collection="messages"] li'


# region docs: browser-message-collection
# examples/mail/browser/messages.py
class InboxPage:
    messages: Annotated[Elements, each(css(MESSAGE))]
    more: Annotated[Element, css('[data-action="more"]')]


@dataclass(frozen=True, slots=True)
class LoadedMessages:
    first_batch: tuple[int, ...]
    all_messages: tuple[int, ...]


async def _ids(messages: Elements) -> tuple[int, ...]:
    values = [await item.attribute("data-message-id") async for item in messages]
    return tuple(int(value) for value in values if value is not None)


async def _load_more(content: InboxPage) -> LoadedMessages:
    first_batch = await _ids(content.messages)
    await content.more.click()
    await content.messages.nth(3).text()
    return LoadedMessages(first_batch, await _ids(content.messages))


async def _mailbox_missing(content: InboxPage) -> NoReturn:
    _ = content
    raise TimeoutError("the inbox did not appear")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadInbox(BrowserOperation[InboxPage, LoadedMessages]):
    __browser__ = Browser.goto(
        "/inbox/",
        InboxPage,
        at=css(MESSAGE),
        outcomes=outcomes(
            when(visible(css(MESSAGE)), then=_load_more),
            otherwise=_mailbox_missing,
        ),
    )


class InboxApi(AsyncBrowserApi):
    read = op(ReadInbox)
# endregion docs: browser-message-collection


# region docs: browser-message-flow
# examples/mail/browser/messages.py
async def read_all_messages() -> LoadedMessages:
    async with playwright_mail() as runtime:
        return await InboxApi(runtime.client).read(options=OPTIONS)
# endregion docs: browser-message-flow


async def run() -> None:
    messages = await read_all_messages()
    print(f"first batch: {list(messages.first_batch)}")
    print(f"after more: {list(messages.all_messages)}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
