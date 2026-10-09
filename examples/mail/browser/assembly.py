"""Hand one Chromium login to the browser and curl_cffi routers of one root."""

from __future__ import annotations

import asyncio

from eazy_sdk import AsyncClient, Identity, bind
from eazy_sdk_browser import BrowserLogin

from examples.mail.browser.session import (
    OPTIONS,
    MailCredentials,
    MailLoginService,
    MailboxApi,
    SessionExpiredError,
)
from examples.mail.http.send import MAIL_BEARER, signing_key
from examples.mail.sdk import BrowserMailSdk
from examples.mail.site._curl import mail_origin
from examples.mail.site._playwright import playwright_mail


# region docs: browser-sdk-flow
# examples/mail/browser/assembly.py
async def use_browser_sdk() -> tuple[int, str, list[int]]:
    login_service = MailLoginService()
    login = BrowserLogin(
        service=login_service,
        cookies=("mail_session",),
        expired=(SessionExpiredError,),
    )
    session = login.session(
        MailCredentials("ada@mail.example", "correct", "123456"),
        identity="ada",
    )

    async with playwright_mail() as runtime:
        browser = runtime.session_client(session)
        try:
            await MailboxApi(browser).inbox(options=OPTIONS)
            state = await session.state(browser)
            token = next(cookie.value for cookie in state.cookies if cookie.name == "mail_session")
            auth = MAIL_BEARER.static(token)
            async with mail_origin(runtime.site) as origin, AsyncClient.curl_cffi(
                base_url=origin,
                impersonate="chrome124",
            ) as http:
                sdk = BrowserMailSdk(
                    http,
                    bindings=(bind(MailboxApi, client=browser),),
                    identity=Identity(auth=(auth,), key_provider=signing_key),
                )
                user = await sdk.account.current()
                page = await sdk.messages.page(offset=0, limit=2)
                await sdk.mailbox.inbox(options=OPTIONS)
        finally:
            await browser.aclose()

    return login_service.calls, user.email, [message.id for message in page.items]
# endregion docs: browser-sdk-flow


async def run() -> None:
    logins, email, messages = await use_browser_sdk()
    print("transport: curl_cffi + Chromium")
    print(f"browser logins: {logins}")
    print(f"user via browser session: {email}")
    print(f"message ids: {messages}")
    print("browser router: mailbox")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
