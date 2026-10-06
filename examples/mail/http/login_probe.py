"""Probe a three-step login declaration and its six documented outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Annotated

import httpx

from eazy_sdk import Client, Http, HttpOperation, JsonField, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import ApiError, Const, Error, Json, Payload

from examples.mail.site import handle_httpx

BASE_URL = "https://mail.example"
WRONG_INPUT = "wrong"
VALID_INPUT = "correct"


@dataclass(frozen=True, slots=True)
class PasswordStep:
    login_id: str


@dataclass(frozen=True, slots=True)
class OtpStep:
    login_id: str
    destination: str


@dataclass(frozen=True, slots=True)
class Session:
    access_token: str


@dataclass(frozen=True, slots=True)
class PasswordStepResponse:
    ok: Annotated[bool, Const(True)]
    result: Payload[PasswordStep]


@dataclass(frozen=True, slots=True)
class OtpStepResponse:
    ok: Annotated[bool, Const(True)]
    result: Payload[OtpStep]


@dataclass(frozen=True, slots=True)
class SessionResponse:
    ok: Annotated[bool, Const(True)]
    result: Payload[Session]


@dataclass(frozen=True, slots=True)
class WrongPasswordBody:
    ok: Annotated[bool, Const(False)]
    code: Annotated[str, Const("wrong_password")]
    message: str


@dataclass(frozen=True, slots=True)
class AccountNotFoundBody:
    ok: Annotated[bool, Const(False)]
    code: Annotated[str, Const("account_not_found")]
    message: str


@dataclass(frozen=True, slots=True)
class AccountBlockedBody:
    ok: Annotated[bool, Const(False)]
    code: Annotated[str, Const("account_blocked")]
    message: str


@dataclass(frozen=True, slots=True)
class WrongCodeBody:
    ok: Annotated[bool, Const(False)]
    code: Annotated[str, Const("wrong_code")]
    message: str


@dataclass(frozen=True, slots=True)
class CaptchaRequiredBody:
    ok: Annotated[bool, Const(False)]
    code: Annotated[str, Const("captcha_required")]
    message: str


class WrongPassword(ApiError[WrongPasswordBody]):
    pass


class AccountNotFound(ApiError[AccountNotFoundBody]):
    pass


class AccountBlocked(ApiError[AccountBlockedBody]):
    pass


class WrongCode(ApiError[WrongCodeBody]):
    pass


class CaptchaRequired(ApiError[CaptchaRequiredBody]):
    pass


class LoginService:
    """Failures shared by all three operations of the login service."""

    base_url = BASE_URL
    errors = (
        Error(200, Json(WrongPasswordBody), exception=WrongPassword),
        Error(200, Json(AccountNotFoundBody), exception=AccountNotFound),
        Error(200, Json(AccountBlockedBody), exception=AccountBlocked),
        Error(200, Json(WrongCodeBody), exception=WrongCode),
        Error(200, Json(CaptchaRequiredBody), exception=CaptchaRequired),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Identify(HttpOperation[PasswordStep]):
    __http__ = Http.post("/login/identify", success={200: Json(PasswordStepResponse)})

    email: JsonField[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitPassword(HttpOperation[OtpStep]):
    __http__ = Http.post("/login/password", success={200: Json(OtpStepResponse)})

    login_id: JsonField[str]
    password: JsonField[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitOtp(HttpOperation[Session]):
    __http__ = Http.post("/login/otp", success={200: Json(SessionResponse)})

    login_id: JsonField[str]
    code: JsonField[str]


# region docs: http-login-router
class LoginApi(LoginService, SyncApi):
    identify = op(Identify)
    password = op(SubmitPassword)
    otp = op(SubmitOtp)
# endregion docs: http-login-router


def _client() -> Client:
    raw = httpx.Client(
        transport=httpx.MockTransport(handle_httpx),
        headers={},
        cookies={},
    )
    return Client(handler=HttpxHandler(raw, owns_client=True))


def _print_error(label: str, call: Callable[[], object]) -> None:
    try:
        call()
    except ApiError as error:
        print(f"{label}: {type(error).__name__}")


def main() -> None:
    with _client() as client:
        login = LoginApi(client)

        password_step = login.identify(email="ada@mail.example")
        _print_error(
            "wrong password",
            lambda: login.password(login_id=password_step.login_id, password=WRONG_INPUT),
        )
        _print_error(
            "account not found",
            lambda: login.identify(email="missing@mail.example"),
        )
        _print_error(
            "account blocked",
            lambda: login.identify(email="blocked@mail.example"),
        )

        otp_step = login.password(login_id=password_step.login_id, password=VALID_INPUT)
        print(f"second factor: {type(otp_step).__name__}")
        _print_error(
            "wrong code",
            lambda: login.otp(login_id=otp_step.login_id, code="000000"),
        )
        _print_error(
            "captcha",
            lambda: login.identify(email="captcha@mail.example"),
        )


if __name__ == "__main__":
    main()
