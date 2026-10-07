"""Shared public declarations and hidden local service for auth guide examples."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated

import httpx
from pydantic import BaseModel, SecretStr

from eazy_sdk import (
    AsyncApi,
    AsyncRoot,
    Http,
    HttpOperation,
    Identity,
    JsonField,
    api_group,
    op,
)
from eazy_sdk.auth import AuthContext, Bearer, ExpiresAt, RefreshToken, session_scheme
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.response import ApiError


# region docs: auth-lifecycle-models
# examples/docs/auth_lifecycle.py
class LoginCredentials(BaseModel):
    email: str
    password: SecretStr


class UserSession(BaseModel):
    access_token: Annotated[SecretStr, Bearer()]
    refresh_token: Annotated[SecretStr, RefreshToken()]
    expires_at: Annotated[datetime, ExpiresAt()]


class Account(BaseModel):
    id: str
    email: str


USER_SESSION = session_scheme(UserSession, name="mail-session")
# endregion docs: auth-lifecycle-models


class AuthProblem(BaseModel):
    message: str


class SessionRejected(ApiError[AuthProblem]):
    pass


# region docs: auth-lifecycle-operations
# examples/docs/auth_lifecycle.py
@dataclass(frozen=True, slots=True, kw_only=True)
class Login(HttpOperation[UserSession]):
    __http__ = Http.post("/auth/login", security=None)

    email: JsonField[str]
    password: JsonField[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class Refresh(HttpOperation[UserSession]):
    __http__ = Http.post("/auth/refresh", security=None)

    refresh_token: JsonField[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class GetAccount(HttpOperation[Account]):
    __http__ = Http.get("/account", errors={401: SessionRejected})


class AuthApi(AsyncApi):
    login = op(Login)
    refresh = op(Refresh)


class AccountApi(AsyncApi):
    security = USER_SESSION
    get_account = op(GetAccount)
# endregion docs: auth-lifecycle-operations


# region docs: auth-lifecycle-service
# examples/docs/auth_lifecycle.py
class LoginService:
    async def acquire(
        self,
        credentials: LoginCredentials,
        context: AuthContext[MailSdk],
    ) -> UserSession:
        return await context.sdk.auth.login(
            email=credentials.email,
            password=credentials.password.get_secret_value(),
        )

    async def refresh(
        self,
        session: UserSession,
        context: AuthContext[MailSdk],
    ) -> UserSession:
        return await context.sdk.auth.refresh(
            refresh_token=session.refresh_token.get_secret_value(),
        )


class MailSdk(AsyncRoot):
    auth = api_group(AuthApi)
    account = api_group(AccountApi)
# endregion docs: auth-lifecycle-service


@dataclass(slots=True)
class AuthSite:
    calls: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        authorization = request.headers.get("Authorization", "")
        self.calls.append(f"{request.method} {request.url.path} {authorization}".rstrip())
        if request.url.path == "/auth/login":
            document = json.loads(request.content)
            return self._session("access-1", "refresh-1", account=document["email"])
        if request.url.path == "/auth/refresh":
            document = json.loads(request.content)
            if not document["refresh_token"]:
                return httpx.Response(401, json={"message": "invalid refresh token"})
            return self._session("access-2", "refresh-2")
        if authorization == "Bearer access-1":
            return httpx.Response(401, json={"message": "expired session"})
        if authorization in {"Bearer access-2", "Bearer saved-access"}:
            return httpx.Response(
                200,
                json={"id": "user-42", "email": "ada@mail.example"},
            )
        return httpx.Response(401, json={"message": "missing session"})

    @staticmethod
    def _session(access: str, refresh: str, *, account: str | None = None) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": "2099-01-01T00:00:00Z",
                "account": account,
            },
        )


@asynccontextmanager
async def mail_sdk(
    site: AuthSite,
    *,
    credentials: LoginCredentials | None = None,
    session: UserSession | None = None,
) -> AsyncIterator[MailSdk]:
    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    auth = USER_SESSION.configure(
        credentials=credentials,
        session=session,
        service=LoginService(),
    )
    async with MailSdk.from_handler(
        handler=AsyncHttpxHandler(raw, owns_client=True),
        base_url="https://api.mail.example",
        identity=Identity(auth=(auth,)),
    ) as sdk:
        yield sdk


def saved_session(*, access: str = "saved-access", refresh: str = "refresh-1") -> UserSession:
    return UserSession(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
