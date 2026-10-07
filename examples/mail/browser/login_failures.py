"""Execute all six browser-login outcomes against the teaching mail site."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from eazy_sdk_browser import BrowserCallOptions, PageError

from examples.mail.browser.login_probe import (
    VALID_LOGIN_INPUT,
    LoginPortal,
    OtpStep,
    PasswordStep,
)
from examples.mail.site._playwright import MailBrowser, playwright_mail

OPTIONS = BrowserCallOptions(timeout=5.0)
REJECTED_INPUT = "wrong"


async def _portal(runtime: MailBrowser) -> LoginPortal:
    await runtime.reset()
    return LoginPortal(runtime.client)


async def _password(portal: LoginPortal, email: str = "ada@mail.example") -> PasswordStep:
    return await portal.identify(email=email, options=OPTIONS)


async def _otp(portal: LoginPortal, email: str = "ada@mail.example") -> OtpStep:
    password = await _password(portal, email)
    return await password.api.password(password=VALID_LOGIN_INPUT, options=OPTIONS)


async def _report(label: str, result: Awaitable[object]) -> None:
    try:
        value = await result
    except PageError as error:
        print(f"{label}: {type(error).__name__}")
    else:
        print(f"{label}: {type(value).__name__}")


async def run() -> None:
    async with playwright_mail() as runtime:
        portal = await _portal(runtime)
        password = await _password(portal)
        await _report(
            "wrong password",
            password.api.password(password=REJECTED_INPUT, options=OPTIONS),
        )
        await _report(
            "account not found",
            _password(await _portal(runtime), "missing@mail.example"),
        )
        await _report(
            "account blocked",
            _password(await _portal(runtime), "blocked@mail.example"),
        )
        await _report("second factor", _otp(await _portal(runtime)))

        otp = await _otp(await _portal(runtime))
        await _report("wrong code", otp.api.otp(code="000000", options=OPTIONS))

        captcha_password = await _password(
            await _portal(runtime),
            "captcha@mail.example",
        )
        await _report(
            "captcha",
            captcha_password.api.password(password=VALID_LOGIN_INPUT, options=OPTIONS),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
