"""Live Chromium adapter for browser tutorial examples; never included in docs pages."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from playwright.async_api import async_playwright

from eazy_sdk_browser import AsyncBrowserClient, SessionSource
from eazy_sdk_browser.handlers.playwright import PlaywrightDriver

from . import PageRoute, intercept_page
from .app import MailSite

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from playwright.async_api import Browser, Page, Route


@dataclass(slots=True)
class MailBrowser:
    browser: Browser
    page: Page
    client: AsyncBrowserClient
    site: MailSite

    async def reset(self) -> None:
        self.site = MailSite()
        await self.page.context.clear_cookies()
        await self.page.goto("https://mail.example/login/")

    def session_client[TRevision](self, session: SessionSource[TRevision]) -> AsyncBrowserClient:
        return AsyncBrowserClient(
            PlaywrightDriver(self.page, capture=None),
            base_url="https://mail.example",
            session=session,
        )

    async def new_tab[TRevision](self, session: SessionSource[TRevision]) -> AsyncBrowserClient:
        page = await self.page.context.new_page()
        return AsyncBrowserClient(
            PlaywrightDriver(page, capture=None),
            base_url="https://mail.example",
            session=session,
        )


@asynccontextmanager
async def playwright_mail() -> AsyncIterator[MailBrowser]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        runtime = MailBrowser(
            browser,
            page,
            AsyncBrowserClient(
                PlaywrightDriver(page, capture=None),
                base_url="https://mail.example",
            ),
            MailSite(),
        )

        async def serve(route: Route) -> None:
            await intercept_page(cast("PageRoute", route), runtime.site)

        await context.route("https://mail.example/**", serve)
        await runtime.reset()
        try:
            yield runtime
        finally:
            await runtime.client.aclose()
            await browser.close()


__all__ = ["MailBrowser", "playwright_mail"]
