"""Run one browser operation through the Pydoll 3 adapter."""

from __future__ import annotations

import asyncio

from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions

from eazy_sdk_browser import AsyncBrowserClient
from eazy_sdk_browser.handlers.pydoll import PydollDriver

from examples.mail.browser.messages import InboxApi, OPTIONS
from examples.mail.site import MailSite
from examples.mail.site._curl import mail_origin


async def main() -> None:
    async with mail_origin(MailSite()) as base_url:
        # docs:integration-pydoll:start
        # examples/docs/integration_pydoll.py
        options = ChromiumOptions()  # type: ignore[no-untyped-call]
        options.headless = True
        browser = Chrome(options=options)
        tab = await browser.start()
        try:
            async with AsyncBrowserClient(
                PydollDriver(tab),
                base_url=base_url,
                owns_driver=True,
            ) as client:
                messages = await InboxApi(client).read(options=OPTIONS)
        finally:
            await browser.stop()  # type: ignore[no-untyped-call]
        # docs:integration-pydoll:end

    print(f"messages: {list(messages.all_messages)}")
    print(f"driver: {client.driver.profile.name}")


if __name__ == "__main__":
    asyncio.run(main())
