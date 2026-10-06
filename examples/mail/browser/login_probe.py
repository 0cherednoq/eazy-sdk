"""Type probe for a three-step browser login expressed with ``to=`` states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, NoReturn

from eazy_sdk_browser import (
    AsyncBrowserApi,
    Browser,
    BrowserOperation,
    Element,
    css,
    outcomes,
    visible,
    when,
)

from eazy_sdk import op

VALID_LOGIN_INPUT = "correct"


class IdentifyPage:
    email: Annotated[Element, css('input[name="email"]')]
    submit: Annotated[Element, css('button[type="submit"]')]


class PasswordPage:
    password: Annotated[Element, css('input[name="password"]')]
    submit: Annotated[Element, css('button[type="submit"]')]


class OtpPage:
    code: Annotated[Element, css('input[name="code"]')]
    submit: Annotated[Element, css('button[type="submit"]')]


@dataclass(frozen=True, slots=True)
class Mailbox:
    api: LoginPortal


@dataclass(frozen=True, slots=True)
class PasswordStep:
    api: LoginPortal

    async def submit(self, password: str) -> OtpStep:
        return await self.api.password(password=password)


@dataclass(frozen=True, slots=True)
class OtpStep:
    api: LoginPortal

    async def submit(self, code: str) -> Mailbox:
        return await self.api.otp(code=code)


async def _identify_stalled(content: IdentifyPage) -> NoReturn:
    _ = content
    raise TimeoutError("password step did not appear")


async def _password_stalled(content: PasswordPage) -> NoReturn:
    _ = content
    raise TimeoutError("OTP step did not appear")


async def _otp_stalled(content: OtpPage) -> NoReturn:
    _ = content
    raise TimeoutError("mailbox did not appear")


@dataclass(frozen=True, slots=True, kw_only=True)
class Identify(BrowserOperation[IdentifyPage, PasswordStep]):
    __browser__ = Browser.act(
        IdentifyPage,
        at=css('input[name="email"]'),
        outcomes=outcomes(
            when(visible(css('input[name="password"]')), to=PasswordStep),
            otherwise=_identify_stalled,
        ),
    )

    email: str

    async def act(self, content: IdentifyPage) -> None:
        await content.email.fill(self.email)
        await content.submit.click()


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitPassword(BrowserOperation[PasswordPage, OtpStep]):
    __browser__ = Browser.act(
        PasswordPage,
        at=css('input[name="password"]'),
        outcomes=outcomes(
            when(visible(css('input[name="code"]')), to=OtpStep),
            otherwise=_password_stalled,
        ),
    )

    password: str

    async def act(self, content: PasswordPage) -> None:
        await content.password.fill(self.password)
        await content.submit.click()


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitOtp(BrowserOperation[OtpPage, Mailbox]):
    __browser__ = Browser.act(
        OtpPage,
        at=css('input[name="code"]'),
        outcomes=outcomes(
            when(visible(css('[data-page="mailbox"]')), to=Mailbox),
            otherwise=_otp_stalled,
        ),
    )

    code: str

    async def act(self, content: OtpPage) -> None:
        await content.code.fill(self.code)
        await content.submit.click()


# docs: browser-login-router-start
class LoginPortal(AsyncBrowserApi):
    identify = op(Identify)
    password = op(SubmitPassword)
    otp = op(SubmitOtp)
# docs: browser-login-router-end


async def typed_login(portal: LoginPortal) -> Mailbox:
    """Make loss of a step type fail the ordinary mypy gate."""

    password: PasswordStep = await portal.identify(email="ada@mail.example")
    otp: OtpStep = await password.submit(password=VALID_LOGIN_INPUT)
    mailbox: Mailbox = await otp.submit(code="123456")
    return mailbox
