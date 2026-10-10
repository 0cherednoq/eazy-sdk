"""Phase 59.7: the current session and the user's cookies, read from the SDK itself.

The scenario is a webmail SDK whose caller needs the cookies after signing in, to hand them to a
browser tab. Nothing here names a store or the key a session is kept under.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Identity, api_group, op
from eazy_sdk.auth import (
    AuthContext,
    Cookies,
    Placed,
    current_session,
    ensure_session,
    session_scheme,
)
from eazy_sdk.cookies import CookieState, cookies_of
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import AsyncHttpxHandler

BASE = "https://mail.example"


class Token(BaseModel):
    token: str


class Folders(BaseModel):
    folders: list[str]


class MailSession(BaseModel):
    token: Annotated[str, Placed.query("token")]


class OtherSession(BaseModel):
    key: Annotated[str, Placed.header("X-Key")]


MAIL_SESSION = session_scheme(MailSession, name="mail-session")
OTHER_SESSION = session_scheme(OtherSession, name="other-session")


@dataclass(frozen=True, slots=True, kw_only=True)
class SignIn(HttpOperation[Token]):
    __http__ = Http.post("/login")


@dataclass(frozen=True, slots=True, kw_only=True)
class ListFolders(HttpOperation[Folders]):
    __http__ = Http.get("/api/folders")


class LoginApi(AsyncApi):
    sign_in = op(SignIn)


class MailApi(AsyncApi):
    security = MAIL_SESSION

    folders = op(ListFolders)


class MailService:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self, credentials: str, context: AuthContext[Any]) -> MailSession:
        self.acquired += 1
        await asyncio.sleep(0)
        return MailSession(token=(await context.sdk.login.sign_in()).token)


class OtherService:
    async def acquire(self, credentials: str, context: AuthContext[Any]) -> OtherSession:
        return OtherSession(key="k")


class MailSdk(AsyncRoot):
    cookies = Cookies()

    login = api_group(LoginApi)
    mail = api_group(MailApi)


def _handler(seen: list[str]) -> AsyncHttpxHandler:
    def site(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/login":
            return httpx.Response(
                200, json={"token": "t1"}, headers={"set-cookie": "sid=s1; Path=/"}
            )
        return httpx.Response(200, json={"folders": ["Inbox"]})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    return AsyncHttpxHandler(raw, owns_client=True)


def _sdk(seen: list[str], service: MailService, *extra: Any) -> MailSdk:
    auth = MAIL_SESSION.configure(credentials="ada", service=service)
    return MailSdk.from_handler(
        handler=_handler(seen), base_url=BASE, identity=Identity(auth=(auth, *extra))
    )


@pytest.mark.asyncio
async def test_before_signing_in_there_is_no_session_and_no_cookie() -> None:
    seen: list[str] = []
    async with _sdk(seen, MailService()) as sdk:
        assert await current_session(sdk) is None
        assert cookies_of(sdk).is_empty()
    assert seen == []


@pytest.mark.asyncio
async def test_ensure_session_signs_in_without_a_call_of_the_callers_own() -> None:
    seen: list[str] = []
    service = MailService()
    async with _sdk(seen, service) as sdk:
        session = await ensure_session(sdk)
        assert session == MailSession(token="t1")
        assert await current_session(sdk) == session
        cookies = cookies_of(sdk)
        await sdk.mail.folders()
        assert await ensure_session(sdk) == session

    assert seen == ["/login", "/api/folders"]
    assert service.acquired == 1
    assert isinstance(cookies, CookieState)
    assert [(cookie.name, cookie.value) for cookie in cookies] == [("sid", "s1")]


@pytest.mark.asyncio
async def test_callers_that_ask_at_once_share_one_sign_in() -> None:
    seen: list[str] = []
    service = MailService()
    async with _sdk(seen, service) as sdk:
        sessions = await asyncio.gather(*(ensure_session(sdk) for _ in range(5)))
    assert service.acquired == 1
    assert len({session.token for session in sessions}) == 1


@pytest.mark.asyncio
async def test_a_router_is_asked_the_same_way_as_the_root() -> None:
    seen: list[str] = []
    async with _sdk(seen, MailService()) as sdk:
        session = await ensure_session(sdk.mail)
        assert await current_session(sdk.login) == session
        assert not cookies_of(sdk.mail).is_empty()


@pytest.mark.asyncio
async def test_several_sessions_need_a_name() -> None:
    seen: list[str] = []
    other = OTHER_SESSION.configure(credentials="ada", service=OtherService())
    async with _sdk(seen, MailService(), other) as sdk:
        with pytest.raises(PlanError, match=r"several sessions \('mail-session', 'other-session'\)"):
            await ensure_session(sdk)
        assert (await ensure_session(sdk, scheme=MAIL_SESSION)).token == "t1"
        assert await current_session(sdk, scheme=OTHER_SESSION) is None


@pytest.mark.asyncio
async def test_what_holds_no_session_says_so() -> None:
    raw = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    sdk = MailSdk.from_handler(handler=AsyncHttpxHandler(raw, owns_client=True), base_url=BASE)
    async with sdk:
        with pytest.raises(PlanError, match="given no session"):
            await ensure_session(sdk)
        with pytest.raises(PlanError, match="is not a session this SDK was given"):
            await current_session(sdk, scheme=MAIL_SESSION)
    with pytest.raises(TypeError, match="an SDK root or a router"):
        await ensure_session(object())
    with pytest.raises(TypeError, match="an SDK root or a router"):
        cookies_of(object())
