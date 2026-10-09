"""Place one session into the query and the cookies of every protected request."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, Self

import httpx
from pydantic import BaseModel, SecretStr

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Identity, api_group, op
from eazy_sdk.auth import AuthContext, Placed, session_scheme
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from examples.docs.redirect_site import (
    BASE_URL,
    MAILBOX,
    PAGE_TOKEN,
    PASSWORD,
    SESSION_ID,
    RedirectSite,
)


class MailLogin(BaseModel):
    username: str
    password: SecretStr


class Folders(BaseModel):
    folders: list[str]


# region docs: auth-placed-model
# examples/docs/auth_placed.py
class MailSession(BaseModel):
    token: Annotated[SecretStr, Placed.query("token")]
    email: Annotated[str, Placed.query("email", secret=False)]
    cookies: Annotated[dict[str, str], Placed.cookies()]


MAIL_SESSION = session_scheme(MailSession, name="mail-session")


@dataclass(frozen=True, slots=True, kw_only=True)
class ListFolders(HttpOperation[Folders]):
    __http__ = Http.get("/api/folders")


class MailApi(AsyncApi):
    security = MAIL_SESSION

    folders = op(ListFolders)


# endregion docs: auth-placed-model


class MailLoginService:
    """Stands in for a real login: the next guide declares its steps."""

    async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> MailSession:
        return MailSession(
            token=SecretStr(PAGE_TOKEN),
            email=credentials.username,
            cookies={"sid": SESSION_ID},
        )


class MailSdk(AsyncRoot):
    mail = api_group(MailApi)

    @classmethod
    def open(cls, site: RedirectSite, credentials: MailLogin) -> Self:
        auth = MAIL_SESSION.configure(credentials=credentials, service=MailLoginService())
        raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
        return cls.from_handler(
            handler=AsyncHttpxHandler(raw, owns_client=True),
            base_url=BASE_URL,
            identity=Identity(auth=(auth,)),
        )


@dataclass(slots=True)
class RecordingSite(RedirectSite):
    """The teaching site, remembering what the protected request carried."""

    seen: str = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/folders":
            self.seen = f"{request.url.query.decode()} | Cookie: {request.headers.get('cookie')}"
        return RedirectSite.__call__(self, request)


async def main() -> None:
    site = RecordingSite()
    credentials = MailLogin(username=MAILBOX, password=SecretStr(PASSWORD))
    async with MailSdk.open(site, credentials) as sdk:
        folders = await sdk.mail.folders()

    print(f"folders: {', '.join(folders.folders)}")
    print(f"request: {site.seen}")


if __name__ == "__main__":
    asyncio.run(main())
