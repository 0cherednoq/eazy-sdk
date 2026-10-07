"""Execute all six browser-login outcomes against the teaching mail site."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from eazy_sdk_browser import AsyncBrowserClient, BrowserCallOptions, PageError

from examples.mail.browser.login_probe import (
    VALID_LOGIN_INPUT,
    LoginPortal,
    OtpStep,
    PasswordStep,
)
from examples.mail.site.browser_driver import TeachingLoginDriver

OPTIONS = BrowserCallOptions(timeout=0.01)
REJECTED_INPUT = "wrong"


def _portal() -> LoginPortal:
    return LoginPortal(AsyncBrowserClient(TeachingLoginDriver()))


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
    portal = _portal()
    password = await _password(portal)
    await _report(
        "wrong password",
        password.api.password(password=REJECTED_INPUT, options=OPTIONS),
    )
    await _report("account not found", _password(_portal(), "missing@mail.example"))
    await _report("account blocked", _password(_portal(), "blocked@mail.example"))
    await _report("second factor", _otp(_portal()))

    portal = _portal()
    otp = await _otp(portal)
    await _report("wrong code", otp.api.otp(code="000000", options=OPTIONS))

    portal = _portal()
    captcha_password = await _password(portal, "captcha@mail.example")
    await _report(
        "captcha",
        captcha_password.api.password(password=VALID_LOGIN_INPUT, options=OPTIONS),
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
