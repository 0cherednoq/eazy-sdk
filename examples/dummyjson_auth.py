"""Login to DummyJSON, then call a Bearer-protected endpoint."""

from __future__ import annotations

import os
from typing import Annotated, TypedDict, Unpack

from pydantic import BaseModel, Field, SecretStr

from eazy_sdk import Client, ClientConfig, SyncApi, api
from eazy_sdk.auth import BearerScheme
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import JsonField
from eazy_sdk.response import ApiError, Error, Json, Responses, Success

BASE_URL = "https://dummyjson.com"
USER_BEARER = BearerScheme("dummyjson-user")


class LoginCredentials(BaseModel):
    username: str
    password: SecretStr


class LoginRequest(TypedDict):
    username: Annotated[str, JsonField()]
    password: Annotated[str, JsonField()]
    expires_in_mins: Annotated[int, JsonField("expiresInMins")]


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


LOGIN_RESPONSES: Responses[LoginSession] = Responses(
    success=(Success(200, Json(LoginSession)),),
    errors=(Error(400, Json(AuthProblem), exception=LoginRejected),),
)
ME_RESPONSES: Responses[CurrentUser] = Responses(
    success=(Success(200, Json(CurrentUser)),),
    errors=(Error(401, Json(AuthProblem)),),
)


class DummyJsonAuthApi(SyncApi):
    @api.post(
        "/auth/login",
        operation_id="login",
        responses=LOGIN_RESPONSES,
        security=None,
    )
    def login(self, **request: Unpack[LoginRequest]) -> LoginSession:
        raise NotImplementedError


class DummyJsonUsersApi(SyncApi):
    @api.get(
        "/auth/me",
        operation_id="getCurrentUser",
        responses=ME_RESPONSES,
        security=USER_BEARER,
    )
    def me(self) -> CurrentUser:
        raise NotImplementedError


def login(credentials: LoginCredentials) -> LoginSession:
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(),
        config=ClientConfig(timeout=20),
    ) as client:
        return DummyJsonAuthApi(client).login(
            username=credentials.username,
            password=credentials.password.get_secret_value(),
            expires_in_mins=30,
        )


def current_user(session: LoginSession) -> CurrentUser:
    auth = USER_BEARER.static(session.access_token.get_secret_value())
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(),
        config=ClientConfig(auth=auth, timeout=20),
    ) as client:
        return DummyJsonUsersApi(client).me()


def main() -> None:
    # These defaults are public demo credentials from DummyJSON documentation.
    credentials = LoginCredentials(
        username=os.getenv("DUMMYJSON_USERNAME", "emilys"),
        password=SecretStr(os.getenv("DUMMYJSON_PASSWORD", "emilyspass")),
    )

    try:
        session = login(credentials)
    except LoginRejected as error:
        raise SystemExit(f"DummyJSON rejected the demo login: {error.error.message}") from error

    user = current_user(session)

    print(f"authenticated: {user.username} ({user.first_name} {user.last_name})")
    print("access token received and kept out of output")


if __name__ == "__main__":
    main()
