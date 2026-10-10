"""Tell success, a stale token and a refusal apart when the service answers 200 to everything."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Identity, Query, api_group, op
from eazy_sdk.auth import AuthContext, Placed, session_scheme
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.response import ApiError, Const, Not, OneOf, Payload

BASE_URL = "https://mail.example"


@dataclass(slots=True)
class EnvelopeSite:
    """Answers 200 always; the outcome is the ``status`` field of the envelope."""

    issued: int = 0
    calls: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/token":
            self.issued += 1
            return httpx.Response(200, json={"token": f"t{self.issued}"})
        token = request.url.params.get("token", "")
        self.calls.append(f"{path} {token}")
        if token != f"t{self.issued}":
            return httpx.Response(200, json={"status": 403, "body": "token"})
        if request.url.params.get("sort") == "wrong":
            return httpx.Response(200, json={"status": 400, "body": {"sort": "invalid"}})
        return httpx.Response(200, json={"status": 200, "body": {"folders": ["Inbox", "Sent"]}})

    def expire_token(self) -> None:
        self.issued += 1


class Token(BaseModel):
    token: str


class Folders(BaseModel):
    folders: list[str]


class MailSession(BaseModel):
    token: Annotated[str, Placed.query("token")]


MAIL_SESSION = session_scheme(MailSession, name="mail-session")


# region docs: response-envelope-status
# examples/docs/response_envelope_status.py
class Ok[T](BaseModel):
    status: Annotated[int, Const(200)]
    body: Payload[T]


class TokenRefused(BaseModel):
    status: Annotated[int, OneOf(401, 403)]


class Refused(BaseModel):
    status: Annotated[int, Not(200, 401, 403)]
    body: Any = None


class TokenRejected(ApiError[TokenRefused]):
    reauth = True


class MailRefused(ApiError[Refused]):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ListFolders(HttpOperation[Folders]):
    __http__ = Http.get("/api/folders", success={200: Ok[Folders]})

    sort: Query[str] = "name"


class MailApi(AsyncApi):
    security = MAIL_SESSION
    errors = ({200: [TokenRejected, MailRefused]},)

    folders = op(ListFolders)
# endregion docs: response-envelope-status


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadToken(HttpOperation[Token]):
    __http__ = Http.get("/token")


class LoginApi(AsyncApi):
    token = op(ReadToken)


class MailService:
    async def acquire(self, credentials: str, context: AuthContext[Any]) -> MailSession:
        return MailSession(token=(await context.sdk.login.token()).token)

    async def refresh(self, session: MailSession, context: AuthContext[Any]) -> MailSession:
        return MailSession(token=(await context.sdk.login.token()).token)


class MailSdk(AsyncRoot):
    login = api_group(LoginApi)
    mail = api_group(MailApi)


async def main() -> None:
    site = EnvelopeSite()
    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    auth = MAIL_SESSION.configure(credentials="ada", service=MailService())
    sdk = MailSdk.from_handler(
        handler=AsyncHttpxHandler(raw, owns_client=True),
        base_url=BASE_URL,
        identity=Identity(auth=(auth,)),
    )
    async with sdk:
        print("folders:", ", ".join((await sdk.mail.folders()).folders))
        site.expire_token()
        print("after the token expired:", ", ".join((await sdk.mail.folders()).folders))
        try:
            await sdk.mail.folders(sort="wrong")
        except MailRefused as error:
            print(f"refused: status {error.error.status}, details {error.error.body}")
    print("calls:", "; ".join(site.calls))


if __name__ == "__main__":
    asyncio.run(main())
