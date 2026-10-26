"""Phase 62: the sign-in service puts into the jar a cookie that a script of the page would set.

The scenario is a webmail whose login answers with a token in JSON and sets no cookie; the page
script writes the token into the cookie ``rsid``, and every API call is accepted on that cookie
alone. No operation names the cookie, and no request header is built by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Self

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Identity, JsonField, api_group, op
from eazy_sdk.auth import AuthContext, Placed, session_scheme
from eazy_sdk.auth.session import (
    MemorySessionStore,
    SessionConfigurationError,
    SessionKey,
    SessionRevision,
)
from eazy_sdk.cookies import Cookies, CookieState, StoredCookie, cookies_of
from eazy_sdk.handlers.httpx import AsyncHttpxHandler

BASE = "https://mail.example"
HOST = "mail.example"
KEY = SessionKey("session:client")


@dataclass(slots=True)
class ScriptCookieSite:
    """Hands out a token on login and serves the API only to the cookie ``rsid=<token>``."""

    logins: int = 0
    seen: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        cookies = dict(
            pair.split("=", 1) for pair in request.headers.get("cookie", "").split("; ") if pair
        )
        path = request.url.path
        self.seen.append((path, request.headers.get("cookie", "")))
        if path == "/login":
            self.logins += 1
            return httpx.Response(200, json={"token": f"t{self.logins}"})
        accepted = cookies.get("rsid") == f"t{self.logins}"
        return httpx.Response(200 if accepted else 401, json={"uid": 7, "status": "OK"})


class Token(BaseModel):
    token: str


class Sent(BaseModel):
    uid: int
    status: str


class MailLogin(BaseModel):
    email: str


class MailSession(BaseModel):
    """What the caller wants to know; the token travels in the jar, not through a placement."""

    email: str


MAIL_SESSION = session_scheme(MailSession, name="mail-session")


@dataclass(frozen=True, slots=True, kw_only=True)
class SignIn(HttpOperation[Token]):
    __http__ = Http.post("/login", security=None)

    email: JsonField[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class Compose(HttpOperation[Sent]):
    __http__ = Http.post("/ajax/compose/")

    subject: JsonField[str]


class AuthApi(AsyncApi):
    sign_in = op(SignIn)


class MailApi(AsyncApi):
    security = MAIL_SESSION

    send = op(Compose)


def _rsid(value: str, *, expires_at: datetime | None = None) -> StoredCookie:
    return StoredCookie("rsid", value, HOST, secure=True, expires_at=expires_at)


class ScriptCookieService:
    """Signs in, then does what the page script does: writes the token into ``rsid``."""

    async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> MailSession:
        signed = await context.sdk.auth.sign_in(email=credentials.email)
        context.set_cookie(_rsid(signed.token))
        return MailSession(email=credentials.email)


class MailSdk(AsyncRoot):
    cookies = Cookies(required=("rsid",))

    auth = api_group(AuthApi)
    mail = api_group(MailApi)

    @classmethod
    def open(cls, site: ScriptCookieSite, store: MemorySessionStore[MailSession]) -> Self:
        auth = MAIL_SESSION.configure(
            credentials=MailLogin(email="ada@mail.example"),
            service=ScriptCookieService(),
            store=store,
        )
        raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
        return cls.from_handler(
            handler=AsyncHttpxHandler(raw, owns_client=True),
            base_url=BASE,
            identity=Identity(auth=(auth,)),
        )


# --- the cookie travels like one a server set -------------------------------------------------


@pytest.mark.asyncio
async def test_a_cookie_the_service_sets_goes_with_the_next_request_and_signs_in_once() -> None:
    site, store = ScriptCookieSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(site, store) as sdk:
        assert (await sdk.mail.send(subject="hi")).status == "OK"
        await sdk.mail.send(subject="again")

    assert site.logins == 1
    assert site.seen == [
        ("/login", ""),
        ("/ajax/compose/", "rsid=t1"),
        ("/ajax/compose/", "rsid=t1"),
    ]


@pytest.mark.asyncio
async def test_the_cookie_is_saved_with_the_session_and_read_back_by_cookies_of() -> None:
    site, store = ScriptCookieSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.send(subject="hi")
        assert [(cookie.name, cookie.value) for cookie in cookies_of(sdk)] == [("rsid", "t1")]

    stored = await store.load(KEY)
    assert stored is not None and stored.revision == SessionRevision(1)
    assert stored.cookies is not None
    assert [(cookie.name, cookie.domain, cookie.secure) for cookie in stored.cookies] == [
        ("rsid", HOST, True)
    ]


@pytest.mark.asyncio
async def test_a_new_process_continues_on_the_saved_cookie_without_a_login() -> None:
    site, store = ScriptCookieSite(), MemorySessionStore[MailSession]()
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.send(subject="hi")
    site.seen.clear()

    async with MailSdk.open(site, store) as again:
        await again.mail.send(subject="again")

    assert site.logins == 1
    assert site.seen == [("/ajax/compose/", "rsid=t1")]


# --- required: the cookie's expiry is the session's ---------------------------------------------


@pytest.mark.asyncio
async def test_a_session_whose_service_cookie_expired_is_acquired_again() -> None:
    site, store = ScriptCookieSite(), MemorySessionStore[MailSession]()
    expired = datetime.now(UTC) - timedelta(minutes=1)
    await store.save(
        KEY,
        MailSession(email="ada@mail.example"),
        SessionRevision(3),
        cookies=CookieState((_rsid("t0", expires_at=expired),)),
    )
    site.logins = 0
    async with MailSdk.open(site, store) as sdk:
        await sdk.mail.send(subject="hi")

    assert site.logins == 1
    assert site.seen[-1] == ("/ajax/compose/", "rsid=t1")
    stored = await store.load(KEY)
    assert stored is not None and stored.revision == SessionRevision(4)


class RewritingService:
    """Writes ``rsid`` twice, as a page script that first stores a draft value would."""

    async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> MailSession:
        signed = await context.sdk.auth.sign_in(email=credentials.email)
        context.set_cookie(_rsid("draft"))
        context.set_cookie(_rsid(signed.token))
        return MailSession(email=credentials.email)


@pytest.mark.asyncio
async def test_a_cookie_set_again_under_the_same_key_replaces_the_first() -> None:
    site = ScriptCookieSite()
    auth = MAIL_SESSION.configure(
        credentials=MailLogin(email="ada@mail.example"), service=RewritingService()
    )
    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    sdk = MailSdk.from_handler(
        handler=AsyncHttpxHandler(raw, owns_client=True),
        base_url=BASE,
        identity=Identity(auth=(auth,)),
    )
    async with sdk:
        await sdk.mail.send(subject="hi")
        assert [(cookie.name, cookie.value) for cookie in cookies_of(sdk)] == [("rsid", "t1")]

    assert site.seen[-1] == ("/ajax/compose/", "rsid=t1")


# --- D-62-01 -------------------------------------------------------------------------------


class TokenSession(BaseModel):
    token: Annotated[str, Placed.query("token")]


TOKEN_SESSION = session_scheme(TokenSession, name="token-session")


class NoJarService:
    async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> TokenSession:
        context.set_cookie(_rsid("t"))
        return TokenSession(token="t")


class TokenApi(AsyncApi):
    security = TOKEN_SESSION

    send = op(Compose)


class NoCookiesSdk(AsyncRoot):
    mail = api_group(TokenApi)


@pytest.mark.asyncio
async def test_set_cookie_without_cookies_on_the_service_names_the_missing_declaration() -> None:
    auth = TOKEN_SESSION.configure(credentials=MailLogin(email="ada"), service=NoJarService())
    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(ScriptCookieSite()), headers={}, cookies={}
    )
    sdk = NoCookiesSdk.from_handler(
        handler=AsyncHttpxHandler(raw, owns_client=True),
        base_url=BASE,
        identity=Identity(auth=(auth,)),
    )
    async with sdk:
        with pytest.raises(SessionConfigurationError, match=r"set_cookie\('rsid'\).*Cookies"):
            await sdk.mail.send(subject="hi")
