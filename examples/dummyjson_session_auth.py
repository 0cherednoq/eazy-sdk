"""Automatic login and refresh hidden behind a small SDK root factory.

The local server follows DummyJSON's documented auth contract. It deliberately
rejects the first access token so the example also demonstrates 401 refresh and
request replay without making a public network dependency part of the example.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Annotated, Self, TypedDict, Unpack

import httpx
from pydantic import BaseModel, Field, SecretStr
from zapros import AsyncBaseHandler

from eazy_sdk import ApiDefaults, AsyncApi, AsyncClient, ClientConfig, api
from eazy_sdk.auth import (
    AuthContext,
    Bearer,
    RefreshToken,
    session_scheme,
)
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import JsonField
from eazy_sdk.response import ApiError, Error, Json, Responses, Success

BASE_URL = "https://dummyjson.com"


class LoginCredentials(BaseModel):
    username: str
    password: SecretStr


class LoginRequest(TypedDict):
    username: Annotated[str, JsonField()]
    password: Annotated[str, JsonField()]
    expires_in_mins: Annotated[int, JsonField("expiresInMins")]


class RefreshRequest(TypedDict):
    refresh_token: Annotated[str, JsonField("refreshToken")]
    expires_in_mins: Annotated[int, JsonField("expiresInMins")]


class UserSession(BaseModel):
    access_token: Annotated[SecretStr, Bearer()] = Field(validation_alias="accessToken")
    refresh_token: Annotated[SecretStr, RefreshToken()] = Field(
        validation_alias="refreshToken"
    )


DUMMYJSON_SESSION = session_scheme(UserSession, name="dummyjson-session")


class CurrentUser(BaseModel):
    id: int
    username: str
    email: str
    first_name: str = Field(validation_alias="firstName")
    last_name: str = Field(validation_alias="lastName")


class AuthProblem(BaseModel):
    message: str


class SessionRejected(ApiError[AuthProblem]):
    pass


SESSION_RESPONSES: Responses[UserSession] = Responses(
    success=(Success(200, Json(UserSession)),)
)
ME_RESPONSES: Responses[CurrentUser] = Responses(
    success=(Success(200, Json(CurrentUser)),),
    errors=(Error(401, Json(AuthProblem), exception=SessionRejected),),
)


class DummyJsonAuthApi(AsyncApi):
    @api.post(
        "/auth/login",
        operation_id="login",
        responses=SESSION_RESPONSES,
        security=None,
    )
    async def login(self, **request: Unpack[LoginRequest]) -> UserSession:
        raise NotImplementedError

    @api.post(
        "/auth/refresh",
        operation_id="refreshSession",
        responses=SESSION_RESPONSES,
        security=None,
    )
    async def refresh(self, **request: Unpack[RefreshRequest]) -> UserSession:
        raise NotImplementedError


class DummyJsonUsersApi(AsyncApi):
    defaults = ApiDefaults(security=DUMMYJSON_SESSION)

    @api.get("/auth/me", operation_id="getCurrentUser", responses=ME_RESPONSES)
    async def me(self) -> CurrentUser:
        raise NotImplementedError


class DummyJsonLoginService:
    """Translate lifecycle values into ordinary decorated auth operations."""

    async def acquire(
        self,
        credentials: LoginCredentials,
        context: AuthContext[DummyJsonSdk],
    ) -> UserSession:
        return await context.sdk.auth.login(
            username=credentials.username,
            password=credentials.password.get_secret_value(),
            expires_in_mins=30,
        )

    async def refresh(
        self,
        session: UserSession,
        context: AuthContext[DummyJsonSdk],
    ) -> UserSession:
        return await context.sdk.auth.refresh(
            refresh_token=session.refresh_token.get_secret_value(),
            expires_in_mins=30,
        )


class DummyJsonSdk:
    """Public SDK facade. Consumers do not call login or refresh directly."""

    def __init__(
        self,
        client: AsyncClient,
    ) -> None:
        self._client = client
        self.auth = DummyJsonAuthApi(client)
        self.users = DummyJsonUsersApi(client)

    @classmethod
    def from_handler(
        cls,
        *,
        handler: AsyncBaseHandler,
        credentials: LoginCredentials | None = None,
        session: UserSession | None = None,
        base_url: str = BASE_URL,
        owns_handler: bool = True,
    ) -> Self:
        auth = DUMMYJSON_SESSION.configure(
            credentials=credentials,
            session=session,
            service=DummyJsonLoginService(),
        )
        client = AsyncClient(
            base_url=base_url,
            handler=handler,
            owns_handler=owns_handler,
            config=ClientConfig(auth=auth, timeout=20),
        )
        return client.bind_sdk(cls)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


@dataclass(slots=True)
class DummyJsonSandbox:
    calls: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        authorization = request.headers.get("Authorization", "")
        self.calls.append(f"{request.method} {request.url.path} {authorization}".rstrip())

        if request.url.path == "/auth/login":
            assert not authorization
            assert json.loads(request.content) == {
                "username": "emilys",
                "password": "emilyspass",
                "expiresInMins": 30,
            }
            return httpx.Response(
                200,
                json={"accessToken": "access-1", "refreshToken": "refresh-1"},
            )

        if request.url.path == "/auth/refresh":
            assert not authorization
            assert json.loads(request.content) == {
                "refreshToken": "refresh-1",
                "expiresInMins": 30,
            }
            return httpx.Response(
                200,
                json={"accessToken": "access-2", "refreshToken": "refresh-2"},
            )

        if authorization == "Bearer access-1":
            return httpx.Response(401, json={"message": "Token Expired!"})
        if authorization not in {"Bearer access-2", "Bearer saved-access"}:
            return httpx.Response(401, json={"message": "Invalid token"})
        return httpx.Response(
            200,
            json={
                "id": 1,
                "username": "emilys",
                "email": "emily.johnson@x.dummyjson.com",
                "firstName": "Emily",
                "lastName": "Johnson",
            },
        )


async def main() -> None:
    server = DummyJsonSandbox()
    credentials = LoginCredentials(
        username="emilys",
        password=SecretStr("emilyspass"),
    )

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(server),
        headers={},
        cookies={},
    )
    handler = AsyncHttpxHandler(raw, owns_client=True)
    async with DummyJsonSdk.from_handler(
        handler=handler,
        credentials=credentials,
    ) as sdk:
        # This is the only operation the SDK consumer calls.
        user = await sdk.users.me()

    print(f"authenticated: {user.username} ({user.first_name} {user.last_name})")
    print("runtime:")
    for call in server.calls:
        print(f"- {call}")


if __name__ == "__main__":
    asyncio.run(main())
