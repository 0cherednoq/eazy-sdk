"""Phase 57.3: one session placed into several parts of the request, declared on its model.

The scenario is a webmail whose every API call carries the page token and the mailbox address in
the query and a device key in a header. All three come from one session, and the session comes
from one login.

Cookies are not placed: since phase 58 a cookie the site sets travels by itself
(``tests/unit/test_phase58_cookie_session.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Self

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from eazy_sdk import (
    AsyncApi,
    AsyncClient,
    AsyncRoot,
    Http,
    HttpOperation,
    Identity,
    api_group,
    op,
)
from eazy_sdk.auth import (
    AuthContext,
    Bearer,
    Placed,
    SessionConfigurationError,
    session_scheme,
)
from eazy_sdk.auth.core import AuthLocation
from eazy_sdk.auth.session_runtime import _SessionModel
from eazy_sdk.handlers.httpx import AsyncHttpxHandler

BASE = "https://mail.example"


class MailLogin(BaseModel):
    username: str
    password: str


class MailSession(BaseModel):
    token: Annotated[SecretStr, Placed.query("token")]
    email: Annotated[str, Placed.query("email", secret=False)]
    device: Annotated[str, Placed.header("X-Device")]
    login: str = ""


MAIL_SESSION = session_scheme(MailSession, name="mail-session")


class Inbox(BaseModel):
    owner: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetInbox(HttpOperation[Inbox]):
    __http__ = Http.get("/api/inbox")


class MailApi(AsyncApi):
    security = MAIL_SESSION

    inbox = op(GetInbox)


@dataclass(slots=True)
class MailLoginService:
    """Hands out a fresh token on every login, so a test can tell one session from the next."""

    logins: int = 0
    refreshes: int = 0

    async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> MailSession:
        self.logins += 1
        return MailSession(
            token=SecretStr(f"token-{self.logins}"),
            email=credentials.username,
            device=f"device-{self.logins}",
        )

    async def refresh(self, session: MailSession, context: AuthContext[Any]) -> MailSession:
        self.refreshes += 1
        return session.model_copy(update={"token": SecretStr("token-refreshed")})


class MailSdk(AsyncRoot):
    mail = api_group(MailApi)

    @classmethod
    def open(
        cls,
        server: MailServer,
        service: MailLoginService,
        *,
        credentials: MailLogin | None = None,
        session: MailSession | None = None,
    ) -> Self:
        auth = MAIL_SESSION.configure(credentials=credentials, session=session, service=service)
        raw = httpx.AsyncClient(transport=httpx.MockTransport(server), headers={}, cookies={})
        return cls.from_handler(
            handler=AsyncHttpxHandler(raw, owns_client=True),
            base_url=BASE,
            identity=Identity(auth=(auth,)),
        )


@dataclass(slots=True)
class MailServer:
    """Answers the inbox to whoever carries a token it accepts, and records what it was sent."""

    rejected: frozenset[str] = frozenset()
    seen: list[tuple[dict[str, str], str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params)
        self.seen.append((query, request.headers.get("x-device", "")))
        if query.get("token") in self.rejected:
            return httpx.Response(401, json={"owner": ""})
        return httpx.Response(200, json={"owner": query.get("email", "")})


USER = MailLogin(username="user@mail.example", password="secret")


# --- one session, three places ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_login_places_the_session_into_the_query_and_a_header() -> None:
    server, service = MailServer(), MailLoginService()
    async with MailSdk.open(server, service, credentials=USER) as sdk:
        assert await sdk.mail.inbox() == Inbox(owner="user@mail.example")
        await sdk.mail.inbox()

    assert service.logins == 1
    query, device = server.seen[0]
    assert query == {"token": "token-1", "email": "user@mail.example"}
    assert device == "device-1"
    assert server.seen[1] == server.seen[0]


@pytest.mark.asyncio
async def test_a_ready_session_is_placed_without_a_login() -> None:
    server, service = MailServer(), MailLoginService()
    ready = MailSession(token=SecretStr("saved"), email="saved@mail.example", device="d")
    async with MailSdk.open(server, service, session=ready) as sdk:
        assert await sdk.mail.inbox() == Inbox(owner="saved@mail.example")

    assert service.logins == 0
    assert server.seen == [({"token": "saved", "email": "saved@mail.example"}, "d")]


@pytest.mark.asyncio
async def test_a_rejected_session_is_refreshed_and_the_call_replayed() -> None:
    server, service = MailServer(rejected=frozenset({"token-1"})), MailLoginService()
    async with MailSdk.open(server, service, credentials=USER) as sdk:
        assert await sdk.mail.inbox() == Inbox(owner="user@mail.example")

    assert (service.logins, service.refreshes) == (1, 1)
    assert [query["token"] for query, _ in server.seen] == ["token-1", "token-refreshed"]


def test_exactly_one_of_credentials_and_session_is_given() -> None:
    with pytest.raises(ValueError, match="exactly one of credentials or session"):
        MAIL_SESSION.configure(service=MailLoginService())


# --- what the scheme is made of ---------------------------------------------------------------


def test_the_scheme_carries_one_placement_per_marked_field() -> None:
    token, email, device = MAIL_SESSION.placements
    assert (token.location, token.name, token.secret) == (AuthLocation.QUERY, "token", True)
    assert (email.location, email.name, email.secret) == (AuthLocation.QUERY, "email", False)
    assert (device.location, device.name) == (AuthLocation.HEADER, "X-Device")


def test_bearer_is_one_spelling_of_a_placed_header() -> None:
    class WithBearer(BaseModel):
        access: Annotated[str, Bearer()]

    class WithHeader(BaseModel):
        access: Annotated[str, Placed.header("Authorization", prefix="Bearer ")]

    bearer: Any = session_scheme(WithBearer, name="s").placements
    header: Any = session_scheme(WithHeader, name="s").placements
    assert bearer == header


def test_a_bearer_and_other_placements_live_on_one_model() -> None:
    class Mixed(BaseModel):
        access: Annotated[str, Bearer()]
        device: Annotated[str, Placed.header("X-Device", secret=False)]
        tenant: Annotated[str, Placed.query("tenant")]

    scheme = session_scheme(Mixed, name="mixed")
    assert [(item.location.value, item.name) for item in scheme.placements] == [
        ("header", "Authorization"),
        ("header", "X-Device"),
        ("query", "tenant"),
    ]
    session = Mixed(access="a", device="d", tenant="t")
    assert [item.value(session) for item in scheme.placements] == ["Bearer a", "d", "t"]


def test_cookies_are_not_placed() -> None:
    """One way to do one thing: a cookie belongs to the jar or to a ``Cookie[...]`` field."""

    assert not hasattr(Placed, "cookie")
    assert not hasattr(Placed, "cookies")


# --- an empty placed value is no session ------------------------------------------------------


def _valid(session: MailSession) -> bool:
    from datetime import UTC, datetime

    return _SessionModel.compile(MailSession, lambda: datetime.now(UTC)).is_valid(session)


def test_a_session_with_every_placed_value_is_valid() -> None:
    assert _valid(MailSession(token=SecretStr("t"), email="e@x", device="d"))


@pytest.mark.parametrize(
    "session",
    [
        MailSession(token=SecretStr(""), email="e@x", device="d"),
        MailSession(token=SecretStr("t"), email="", device="d"),
        MailSession(token=SecretStr("t"), email="e@x", device=""),
    ],
    ids=["empty-secret", "empty-query", "empty-header"],
)
def test_a_session_with_an_empty_placed_value_is_not_valid(session: MailSession) -> None:
    assert not _valid(session)


def test_a_field_that_is_not_placed_may_be_empty() -> None:
    assert _valid(MailSession(token=SecretStr("t"), email="e@x", device="d", login=""))


@pytest.mark.asyncio
async def test_a_login_that_returns_nothing_to_place_is_not_sent() -> None:
    class EmptyLogin(MailLoginService):
        async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> MailSession:
            self.logins += 1
            return MailSession(token=SecretStr(""), email=credentials.username, device="")

    server, service = MailServer(), EmptyLogin()
    async with MailSdk.open(server, service, credentials=USER) as sdk:
        with pytest.raises(Exception, match="session"):
            await sdk.mail.inbox()
    assert server.seen == []


# --- a wrong declaration is refused where it is written ----------------------------------------


class Nothing(BaseModel):
    """No marker at all: the session of a cookie site, or a model whose markers were forgotten."""

    token: str


NOTHING = session_scheme(Nothing, name="nothing")


class NothingService:
    async def acquire(self, credentials: MailLogin, context: AuthContext[Any]) -> Nothing:
        return Nothing(token="t")


class NothingApi(AsyncApi):
    security = NOTHING

    inbox = op(GetInbox)


@pytest.mark.asyncio
async def test_a_model_that_places_nothing_is_refused_unless_the_site_keeps_cookies() -> None:
    server = MailServer()
    auth = NOTHING.configure(credentials=USER, service=NothingService())
    raw = httpx.AsyncClient(transport=httpx.MockTransport(server), headers={}, cookies={})
    async with AsyncClient(
        base_url=BASE, handler=AsyncHttpxHandler(raw, owns_client=True)
    ) as client:
        with pytest.raises(SessionConfigurationError, match="places nothing into the request"):
            await NothingApi(client, identity=Identity(auth=(auth,))).inbox()
    assert server.seen == []


def test_two_bearer_fields_are_refused() -> None:
    class Twice(BaseModel):
        first: Annotated[str, Bearer()]
        second: Annotated[str, Bearer()]

    with pytest.raises(SessionConfigurationError, match="exactly one Bearer"):
        session_scheme(Twice)


def test_two_fields_placed_in_one_place_are_refused() -> None:
    class SameQuery(BaseModel):
        first: Annotated[str, Placed.query("token")]
        second: Annotated[str, Placed.query("token")]

    class SameHeader(BaseModel):
        first: Annotated[str, Bearer()]
        second: Annotated[str, Placed.header("authorization")]

    with pytest.raises(SessionConfigurationError, match="both placed as query 'token'"):
        session_scheme(SameQuery)
    with pytest.raises(SessionConfigurationError, match="both placed as header 'authorization'"):
        session_scheme(SameHeader)


def test_the_same_name_in_different_places_is_fine() -> None:
    class Spread(BaseModel):
        in_query: Annotated[str, Placed.query("sid")]
        in_header: Annotated[str, Placed.header("sid")]

    assert len(session_scheme(Spread).placements) == 2


def test_a_single_place_does_not_take_a_collection() -> None:
    class MappingValue(BaseModel):
        token: Annotated[dict[str, str], Placed.query("token")]

    with pytest.raises(
        SessionConfigurationError, match=r"Placed\.query\('token'\) on MappingValue"
    ):
        session_scheme(MappingValue)


def test_a_placement_needs_a_name() -> None:
    with pytest.raises(ValueError, match="requires a name"):
        Placed.query("")
    with pytest.raises(ValueError, match="requires a name"):
        Placed.header("")
