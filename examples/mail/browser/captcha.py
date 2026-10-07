"""Solve the browser captcha without leaving the password operation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import NoReturn

from eazy_sdk import op
from eazy_sdk_browser import (
    AsyncBrowserApi,
    Browser,
    BrowserCallOptions,
    BrowserOperation,
    Handle,
    click,
    css,
    outcomes,
    visible,
    when,
)

from examples.mail.browser.login_probe import (
    ACCOUNT_FAILURES,
    PASSWORD_FAILURES,
    VALID_LOGIN_INPUT,
    Identify,
    OtpStep,
    PasswordPage,
    SubmitOtp,
)
from examples.mail.site._playwright import playwright_mail

OPTIONS = BrowserCallOptions(timeout=5.0)


async def _password_stalled(content: PasswordPage) -> NoReturn:
    _ = content
    raise TimeoutError("OTP step did not appear")


# region docs: browser-captcha-operation
# examples/mail/browser/captcha.py
CAPTCHA = Handle(
    when=visible(css('[data-page="captcha"]')),
    do=click(css('[data-action="solve"]')),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitPasswordWithCaptcha(BrowserOperation[PasswordPage, OtpStep]):
    __browser__ = Browser.act(
        PasswordPage,
        at=css('input[name="password"]'),
        handlers=(CAPTCHA,),
        errors=PASSWORD_FAILURES,
        outcomes=outcomes(
            when(visible(css('input[name="code"]')), to=OtpStep),
            otherwise=_password_stalled,
        ),
    )

    password: str

    async def act(self, content: PasswordPage) -> None:
        await content.password.fill(self.password)
        await content.submit.click()


class CaptchaLoginPortal(AsyncBrowserApi):
    errors = ACCOUNT_FAILURES

    identify = op(Identify)
    password = op(SubmitPasswordWithCaptcha)
    otp = op(SubmitOtp)
# endregion docs: browser-captcha-operation


# region docs: browser-captcha-flow
# examples/mail/browser/captcha.py
async def login_after_captcha() -> tuple[str, int, str]:
    async with playwright_mail() as runtime:
        portal = CaptchaLoginPortal(runtime.client)
        password = await portal.identify(email="captcha@mail.example", options=OPTIONS)
        otp = await password.api.password(password=VALID_LOGIN_INPUT, options=OPTIONS)
        return (
            runtime.site.state.captcha_solves[0],
            len(runtime.site.state.password_cookies),
            type(otp).__name__,
        )
# endregion docs: browser-captcha-flow


async def run() -> None:
    challenge, requests, next_step = await login_after_captcha()
    print(f"challenge solved: {challenge}")
    print(f"password submits: {requests}")
    print(f"same operation: {next_step}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
