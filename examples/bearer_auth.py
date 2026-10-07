"""Log in locally, then call a Bearer-protected endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, SecretStr

from eazy_sdk import (
    Client,
    ClientConfig,
    Http,
    HttpOperation,
    Identity,
    JsonField,
    Resilience,
    SyncApi,
    op,
)
from eazy_sdk.auth import BearerScheme
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import markers
from eazy_sdk.response import ApiError

BASE_URL = "https://account.example"
USER_BEARER = BearerScheme("account-user")


class LoginCredentials(BaseModel):
    username: str
    password: SecretStr


class LoginSession(BaseModel):
    username: str
    access_token: SecretStr = Field(validation_alias="accessToken")
    refresh_token: SecretStr = Field(validation_alias="refreshToken")


class CurrentUser(BaseModel):
    id: int
    username: str
    email: str
    first_name: str = Field(validation_alias="firstName")
    last_name: str = Field(validation_alias="lastName")


class AuthProblem(BaseModel):
    message: str


class LoginRejected(ApiError[AuthProblem]):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class Login(HttpOperation[LoginSession]):
    __http__ = Http.post(
        "/auth/login",
        operation_id="login",
        errors={400: LoginRejected},
        security=None,
    )

    username: JsonField[str]
    password: JsonField[str]
    expires_in_mins: Annotated[int, markers.JsonField("expiresInMins")]


@dataclass(frozen=True, slots=True, kw_only=True)
class GetCurrentUser(HttpOperation[CurrentUser]):
    __http__ = Http.get(
        "/auth/me",
        operation_id="getCurrentUser",
        errors={401: AuthProblem},
        security=USER_BEARER,
    )


class AccountAuthApi(SyncApi):
    login = op(Login)


class AccountUsersApi(SyncApi):
    me = op(GetCurrentUser)


def _account_site(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/auth/login":
        return httpx.Response(
            200,
            json={
                "username": "emilys",
                "accessToken": "access-demo",
                "refreshToken": "refresh-demo",
            },
        )
    if request.headers.get("Authorization") != "Bearer access-demo":
        return httpx.Response(401, json={"message": "Invalid token"})
    return httpx.Response(
        200,
        json={
            "id": 1,
            "username": "emilys",
            "email": "emily@account.example",
            "firstName": "Emily",
            "lastName": "Johnson",
        },
    )


def login(credentials: LoginCredentials) -> LoginSession:
    raw = httpx.Client(
        transport=httpx.MockTransport(_account_site),
        headers={},
        cookies={},
    )
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(resilience=Resilience(timeout=20)),
    ) as client:
        return AccountAuthApi(client).login(
            username=credentials.username,
            password=credentials.password.get_secret_value(),
            expires_in_mins=30,
        )


def current_user(session: LoginSession) -> CurrentUser:
    identity = Identity(auth=(USER_BEARER.static(session.access_token.get_secret_value()),))
    raw = httpx.Client(
        transport=httpx.MockTransport(_account_site),
        headers={},
        cookies={},
    )
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(resilience=Resilience(timeout=20)),
    ) as client:
        return AccountUsersApi(client, identity=identity).me()


def main() -> None:
    credentials = LoginCredentials(
        username="emilys",
        password=SecretStr("emilyspass"),
    )

    try:
        session = login(credentials)
    except LoginRejected as error:
        raise SystemExit(f"Login rejected: {error.error.message}") from error

    user = current_user(session)

    print(f"authenticated: {user.username} ({user.first_name} {user.last_name})")
    print("access token received and kept out of output")


if __name__ == "__main__":
    main()
