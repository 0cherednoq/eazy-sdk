"""Share one browser login between two tabs of one Chromium context."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from eazy_sdk import op
from eazy_sdk_browser import (
    AsyncBrowserApi,
    Browser,
    BrowserCallOptions,
    BrowserLogin,
    BrowserLoginContext,
    BrowserOperation,
    BrowserState,
    PageError,
    css,
)

from examples.mail.browser.login_probe import LoginPortal
from examples.mail.site._playwright import playwright_mail

OPTIONS = BrowserCallOptions(timeout=5.0)


class SessionExpiredError(PageError):
    """The mail site no longer accepts the context session."""


# region docs: browser-session-login
# examples/mail/browser/session.py
@dataclass(frozen=True, slots=True)
class MailCredentials:
    email: str
    password: str = field(repr=False)
    otp: str = field(repr=False)


@dataclass(slots=True)
class MailLoginService:
    calls: int = 0

    async def acquire(
        self,
        credentials: MailCredentials,
        context: BrowserLoginContext,
    ) -> BrowserState:
        self.calls += 1
        portal = LoginPortal(context.client)
        password = await portal.identify(email=credentials.email, options=OPTIONS)
        otp = await password.submit(password=credentials.password)
        await otp.submit(code=credentials.otp)
        return await context.export_state()
# endregion docs: browser-session-login


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenInbox(BrowserOperation[None, None]):
    __browser__ = Browser.goto("/inbox/", at=css('[data-page="mailbox"]'))


class MailboxApi(AsyncBrowserApi):
    inbox = op(OpenInbox)


# region docs: browser-session-flow
# examples/mail/browser/session.py
async def one_login_for_two_tabs() -> tuple[int, int, str]:
    service = MailLoginService()
    login = BrowserLogin(
        service=service,
        cookies=("mail_session",),
        expired=(SessionExpiredError,),
    )
    session = login.session(
        MailCredentials("ada@mail.example", "correct", "123456"),
        identity="ada",
    )

    async with playwright_mail() as runtime:
        first = runtime.session_client(session)
        second = await runtime.new_tab(session)
        try:
            await asyncio.gather(
                MailboxApi(first).inbox(options=OPTIONS),
                MailboxApi(second).inbox(options=OPTIONS),
            )
            state = await session.state(first)
        finally:
            await first.aclose()
            await second.aclose()

    cookie = next(item.value for item in state.cookies if item.name == "mail_session")
    return service.calls, 2, cookie
# endregion docs: browser-session-flow


async def run() -> None:
    logins, tabs, cookie = await one_login_for_two_tabs()
    print(f"browser logins: {logins}")
    print(f"tabs in context: {tabs}")
    print(f"session cookie: {cookie}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
