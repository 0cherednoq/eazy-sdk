"""Probe a three-step login declaration and its six documented outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import Http, HttpOperation, JsonField, SyncApi, op
from eazy_sdk.response import ApiError, Const, Error, Json, Payload

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
    status: Annotated[str, Const("ok")]
    body: Payload[PasswordStep]


@dataclass(frozen=True, slots=True)
class OtpStepResponse:
    status: Annotated[str, Const("ok")]
    body: Payload[OtpStep]


@dataclass(frozen=True, slots=True)
class SessionResponse:
    status: Annotated[str, Const("ok")]
    body: Payload[Session]


# region docs: http-login-failures
# examples/mail/http/login_probe.py
@dataclass(frozen=True, slots=True)
class WrongPasswordDetails:
    code: Annotated[str, Const("wrong_password")]
    message: str


@dataclass(frozen=True, slots=True)
class AccountNotFoundDetails:
    code: Annotated[str, Const("account_not_found")]
    message: str


@dataclass(frozen=True, slots=True)
class AccountBlockedDetails:
    code: Annotated[str, Const("account_blocked")]
    message: str


@dataclass(frozen=True, slots=True)
class WrongCodeDetails:
    code: Annotated[str, Const("wrong_code")]
    message: str


@dataclass(frozen=True, slots=True)
class WrongPasswordResponse:
    status: Annotated[str, Const("wrong_password")]
    body: Payload[WrongPasswordDetails]


@dataclass(frozen=True, slots=True)
class AccountNotFoundResponse:
    status: Annotated[str, Const("account_not_found")]
    body: Payload[AccountNotFoundDetails]


@dataclass(frozen=True, slots=True)
class AccountBlockedResponse:
    status: Annotated[str, Const("account_blocked")]
    body: Payload[AccountBlockedDetails]


@dataclass(frozen=True, slots=True)
class WrongCodeResponse:
    status: Annotated[str, Const("wrong_code")]
    body: Payload[WrongCodeDetails]


@dataclass(frozen=True, slots=True)
class CaptchaRequiredResponse:
    kind: Annotated[str, Const("captcha")]
    site_key: str


class WrongPassword(ApiError[WrongPasswordResponse]):
    pass


class AccountNotFound(ApiError[AccountNotFoundResponse]):
    pass


class AccountBlocked(ApiError[AccountBlockedResponse]):
    pass


class WrongCode(ApiError[WrongCodeResponse]):
    pass


class CaptchaRequired(ApiError[CaptchaRequiredResponse]):
    pass


class LoginService:
    """Failures shared by all three operations of the login service."""

    base_url = BASE_URL
    errors = (
        Error(200, Json(WrongPasswordResponse), exception=WrongPassword),
        Error(200, Json(AccountNotFoundResponse), exception=AccountNotFound),
        Error(200, Json(AccountBlockedResponse), exception=AccountBlocked),
        Error(200, Json(WrongCodeResponse), exception=WrongCode),
        Error(403, Json(CaptchaRequiredResponse), exception=CaptchaRequired),
    )
# endregion docs: http-login-failures


# region docs: http-login-operations
# examples/mail/http/login_probe.py
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
# endregion docs: http-login-operations


# region docs: http-login-router
# examples/mail/http/login_probe.py
class LoginApi(LoginService, SyncApi):
    identify = op(Identify)
    password = op(SubmitPassword)
    otp = op(SubmitOtp)
# endregion docs: http-login-router
