"""Run one browser operation through the Playwright adapter."""

from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from eazy_sdk_browser import AsyncBrowserClient
from eazy_sdk_browser.handlers.playwright import PlaywrightDriver

from examples.mail.browser.messages import InboxApi, OPTIONS
from examples.mail.site import MailSite
from examples.mail.site._curl import mail_origin


async def main() -> None:
    async with mail_origin(MailSite()) as base_url, async_playwright() as playwright:
        # docs:integration-playwright:start
        # examples/docs/integration_playwright.py
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        async with AsyncBrowserClient(
            PlaywrightDriver(page),
            base_url=base_url,
            owns_driver=True,
        ) as client:
            messages = await InboxApi(client).read(options=OPTIONS)
        await browser.close()
        # docs:integration-playwright:end

    print(f"messages: {list(messages.all_messages)}")
    print(f"driver: {client.driver.profile.name}")


if __name__ == "__main__":
    asyncio.run(main())
