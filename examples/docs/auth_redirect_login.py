"""Sign in to a site that answers the login with a redirect and keeps its token in the page."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Self

import httpx
from eazy_sdk_html import CSS, Regex
from pydantic import BaseModel, SecretStr

from eazy_sdk import AsyncApi, AsyncRoot, Cookie, Http, HttpOperation, Identity, api_group, op
from eazy_sdk.auth import AuthContext, Placed, session_scheme
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import Form
from eazy_sdk.response import ApiError, Const, FromCookie, Location
from examples.docs.redirect_site import BASE_URL, MAILBOX, PASSWORD, RedirectSite


class MailLogin(BaseModel):
    username: str
    password: SecretStr


class Folders(BaseModel):
    folders: list[str]


# region docs: redirect-login-steps
# examples/docs/auth_redirect_login.py
@dataclass(frozen=True, slots=True)
class SignedIn:
    url: Annotated[str, Location(path="/inbox*")]
    sid: Annotated[str, FromCookie("sid")]


@dataclass(frozen=True, slots=True)
class Banned:
    errno: Annotated[str, Location.query("errno"), Const("25")]


@dataclass(frozen=True, slots=True)
class Rejected:
    fail: Annotated[str, Location.query("fail")]


class AccountBanned(ApiError[Banned]):
    pass


class InvalidCredentials(ApiError[Rejected]):
    pass


class MailPage(BaseModel):
    title: Annotated[str, CSS("title::text"), Const("Mail")]
    token: Annotated[str, Regex(r'"account":\{"token":"([^"]+)"')]
    email: Annotated[str, Regex(r'"account":\{[^}]*"email":"([^"]+)"')]


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCredentials(HttpOperation[SignedIn]):
    __http__ = Http.post(
        "/cgi-bin/auth",
        security=None,
        success={302: SignedIn},
        errors={302: [AccountBanned, InvalidCredentials]},
    )

    username: Form[str]
    password: Form[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenInbox(HttpOperation[MailPage]):
    __http__ = Http.get("/inbox/", security=None)

    sid: Cookie[str]


class LoginApi(AsyncApi):
    submit = op(SubmitCredentials)
    inbox = op(OpenInbox)


# endregion docs: redirect-login-steps


# region docs: redirect-login-session
class MailSession(BaseModel):
    token: Annotated[SecretStr, Placed.query("token")]
    email: Annotated[str, Placed.query("email", secret=False)]
    cookies: Annotated[dict[str, str], Placed.cookies()]


MAIL_SESSION = session_scheme(MailSession, name="mail-session")


class MailLoginService:
    async def acquire(self, credentials: MailLogin, context: AuthContext[MailSdk]) -> MailSession:
        signed_in = await context.sdk.login.submit(
            username=credentials.username,
            password=credentials.password.get_secret_value(),
        )
        page = await context.sdk.login.inbox(sid=signed_in.sid)
        return MailSession(
            token=SecretStr(page.token),
            email=page.email,
            cookies={"sid": signed_in.sid},
        )


# endregion docs: redirect-login-session


@dataclass(frozen=True, slots=True, kw_only=True)
class ListFolders(HttpOperation[Folders]):
    __http__ = Http.get("/api/folders")


class MailApi(AsyncApi):
    security = MAIL_SESSION

    folders = op(ListFolders)


class MailSdk(AsyncRoot):
    login = api_group(LoginApi)
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


# region docs: redirect-login-run
async def main() -> None:
    site = RedirectSite()
    async with MailSdk.open(site, MailLogin(username=MAILBOX, password=SecretStr(PASSWORD))) as sdk:
        folders = await sdk.mail.folders()
        await sdk.mail.folders()
    print(f"folders: {', '.join(folders.folders)}")
    print(*site.calls, sep="\n")

    wrong = MailLogin(username=MAILBOX, password=SecretStr("wrong-password"))
    async with MailSdk.open(RedirectSite(), wrong) as sdk:
        try:
            await sdk.mail.folders()
        except InvalidCredentials as error:
            print(f"wrong password: fail {error.error.fail}")


# endregion docs: redirect-login-run


if __name__ == "__main__":
    asyncio.run(main())
