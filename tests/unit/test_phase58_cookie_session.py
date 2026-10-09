"""Phase 58.2-58.3: one declaration, and the session's cookies travel like in a browser.

The scenario is a webmail spread over two hosts. The auth host signs the user in and sets a
cookie for the whole site and one for itself; a redirect chain crosses to the mail host, which
sets its own on the way. No operation names a cookie.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from eazy_sdk import (
    AsyncClient,
    Client,
    ClientConfig,
    Cookie,
    Header,
    Http,
    HttpOperation,
    Identity,
    Resilience,
    SyncApi,
    SyncRoot,
    api_group,
    op,
)
from eazy_sdk.api import AsyncApi
from eazy_sdk.auth import CookieScheme
from eazy_sdk.cookies import CookieJar, Cookies, CookieState, StoredCookie
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler
from eazy_sdk.request import Form
from eazy_sdk.response import UnexpectedResponseError

AUTH = "https://auth.mail.example"
MAIL = "https://e.mail.example"


@dataclass(slots=True)
class MailSite:
    """Two hosts of one site; remembers the ``Cookie`` header of every request it saw."""

    seen: list[tuple[str, str]] = field(default_factory=list)
    counter: int = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.seen.append((f"{request.method} {host}{path}", request.headers.get("cookie", "")))
        if path == "/login":
            return httpx.Response(
                200,
                json={"ok": True},
                headers=[
                    ("set-cookie", "sid=s1; Domain=mail.example; Path=/"),
                    ("set-cookie", "act=csrf; Path=/"),
                ],
            )
        if path == "/hop1":
            return httpx.Response(
                302,
                headers=[
                    ("location", f"{MAIL}/hop2"),
                    ("set-cookie", "a=1; Domain=mail.example; Path=/"),
                ],
            )
        if path == "/hop2":
            return httpx.Response(
                302, headers=[("location", "/final"), ("set-cookie", "b=2; Path=/")]
            )
        if path == "/denied":
            return httpx.Response(
                401, json={"ok": False}, headers=[("set-cookie", "why=expired; Path=/")]
            )
        if path == "/unique":
            self.counter += 1
            return httpx.Response(
                200, json={"ok": True}, headers=[("set-cookie", f"c{self.counter}=1; Path=/")]
            )
        return httpx.Response(200, json={"cookie": request.headers.get("cookie", "")})

    def cookie_of(self, call: str) -> str:
        return next(cookie for seen, cookie in reversed(self.seen) if seen == call)


@dataclass(frozen=True, slots=True)
class Echo:
    cookie: str


@dataclass(frozen=True, slots=True)
class Ok:
    ok: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class SignIn(HttpOperation[Ok]):
    __http__ = Http.post("/login")

    username: Form[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthEcho(HttpOperation[Echo]):
    __http__ = Http.get("/echo")


@dataclass(frozen=True, slots=True, kw_only=True)
class MailEcho(HttpOperation[Echo]):
    __http__ = Http.get(f"{MAIL}/echo")


@dataclass(frozen=True, slots=True, kw_only=True)
class Chain(HttpOperation[Echo]):
    __http__ = Http.get("/hop1")


@dataclass(frozen=True, slots=True, kw_only=True)
class Denied(HttpOperation[Ok]):
    __http__ = Http.get("/denied")


@dataclass(frozen=True, slots=True, kw_only=True)
class Outside(HttpOperation[Echo]):
    """Opts out: neither reads the jar nor writes into it."""

    __http__ = Http.get("/login-outside", cookies=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class WithOwnCookie(HttpOperation[Echo]):
    """A cookie the caller chooses, on a site whose session travels by itself."""

    __http__ = Http.get("/echo")

    sid: Cookie[str]


class MailApi(SyncApi):
    cookies = Cookies()

    sign_in = op(SignIn)
    auth_echo = op(AuthEcho)
    mail_echo = op(MailEcho)
    chain = op(Chain)
    denied = op(Denied)
    outside = op(Outside)
    with_own = op(WithOwnCookie)


class PlainApi(SyncApi):
    """The same operations on a service that declares nothing about cookies."""

    sign_in = op(SignIn)
    auth_echo = op(AuthEcho)


def _client(site: MailSite, *, max_redirects: int = 0) -> Client:
    raw = httpx.Client(transport=httpx.MockTransport(site), headers={}, cookies={})
    return Client(
        base_url=AUTH,
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(resilience=Resilience(max_redirects=max_redirects)),
    )


def _names(header: str) -> list[str]:
    return sorted(pair.split("=")[0] for pair in header.split("; ") if pair)


# --- between operations -----------------------------------------------------------------------


def test_a_cookie_a_response_sets_goes_out_with_the_next_request() -> None:
    site = MailSite()
    with _client(site) as client:
        api = MailApi(client)
        api.sign_in(username="ada")
        assert _names(api.auth_echo().cookie) == ["act", "sid"]


def test_each_host_receives_only_the_cookies_meant_for_it() -> None:
    site = MailSite()
    with _client(site) as client:
        api = MailApi(client)
        api.sign_in(username="ada")
        assert _names(api.mail_echo().cookie) == ["sid"]


def test_the_first_request_carries_nothing() -> None:
    site = MailSite()
    with _client(site) as client:
        MailApi(client).sign_in(username="ada")
    assert site.seen == [("POST auth.mail.example/login", "")]


# --- between the hops of a redirect -----------------------------------------------------------


def test_a_cookie_set_by_one_hop_reaches_the_next_and_only_its_own_host() -> None:
    site = MailSite()
    with _client(site, max_redirects=3) as client:
        api = MailApi(client)
        api.sign_in(username="ada")
        final = api.chain()

    assert _names(site.cookie_of("GET auth.mail.example/hop1")) == ["act", "sid"]
    assert _names(site.cookie_of("GET e.mail.example/hop2")) == ["a", "sid"]
    assert _names(final.cookie) == ["a", "b", "sid"]
    assert _names(site.cookie_of("GET e.mail.example/final")) == ["a", "b", "sid"]


def test_cookies_are_read_from_a_response_the_operation_rejects() -> None:
    site = MailSite()
    with _client(site) as client:
        api = MailApi(client)
        with pytest.raises(UnexpectedResponseError):
            api.denied()
        assert _names(api.auth_echo().cookie) == ["why"]


# --- a cookie the caller chooses --------------------------------------------------------------


def test_a_declared_cookie_wins_for_one_request_and_is_not_kept() -> None:
    site = MailSite()
    with _client(site) as client:
        api = MailApi(client)
        api.sign_in(username="ada")
        chosen = api.with_own(sid="mine").cookie
        assert "sid=mine" in chosen and "sid=s1" not in chosen
        assert "act=csrf" in chosen
        assert "sid=s1" in api.auth_echo().cookie


def test_an_operation_that_opts_out_neither_reads_nor_writes_the_jar() -> None:
    site = MailSite()
    identity = Identity()
    with _client(site) as client:
        api = MailApi(client, identity=identity)
        api.sign_in(username="ada")
        before = identity._jar.snapshot()
        assert api.outside().cookie == ""
        assert identity._jar.snapshot() == before


# --- whose jar it is --------------------------------------------------------------------------


def test_two_identities_on_one_client_share_no_cookie() -> None:
    site = MailSite()
    with _client(site) as client:
        ada = MailApi(client, identity=Identity())
        bob = MailApi(client, identity=Identity())
        ada.sign_in(username="ada")
        assert _names(ada.auth_echo().cookie) == ["act", "sid"]
        assert bob.auth_echo().cookie == ""


def test_one_identity_on_two_clients_shares_every_cookie() -> None:
    site = MailSite()
    identity = Identity()
    with _client(site) as first, _client(site) as second:
        MailApi(first, identity=identity).sign_in(username="ada")
        assert _names(MailApi(second, identity=identity).auth_echo().cookie) == ["act", "sid"]


def test_routers_of_one_root_share_the_jar_without_an_identity() -> None:
    class LoginGroup(SyncApi):
        sign_in = op(SignIn)

    class MailGroup(SyncApi):
        echo = op(AuthEcho)

    class Sdk(SyncRoot):
        cookies = Cookies()

        login = api_group(LoginGroup)
        mail = api_group(MailGroup)

    site = MailSite()
    with _client(site) as client:
        sdk = Sdk(client)
        sdk.login.sign_in(username="ada")
        assert _names(sdk.mail.echo().cookie) == ["act", "sid"]


def test_an_identity_starts_from_the_cookies_it_is_given() -> None:
    saved = CookieState((StoredCookie("sid", "from-browser", "mail.example", host_only=False),))
    site = MailSite()
    with _client(site) as client:
        api = MailApi(client, identity=Identity(cookies=saved))
        assert api.mail_echo().cookie == "sid=from-browser"


# --- without the declaration nothing changes --------------------------------------------------


def test_a_service_that_declares_nothing_sends_no_cookie_it_was_not_told_to() -> None:
    site = MailSite()
    identity = Identity()
    with _client(site) as client:
        api = PlainApi(client, identity=identity)
        api.sign_in(username="ada")
        assert api.auth_echo().cookie == ""
    assert identity._jar.snapshot().is_empty()


# --- concurrency ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_calls_of_one_identity_lose_no_cookie() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Unique(HttpOperation[Ok]):
        __http__ = Http.get("/unique")

    class Api(AsyncApi):
        cookies = Cookies()

        unique = op(Unique)

    site = MailSite()
    identity = Identity()
    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    async with AsyncClient(
        base_url=AUTH, handler=AsyncHttpxHandler(raw, owns_client=True)
    ) as client:
        api = Api(client, identity=identity)
        await asyncio.gather(*(api.unique() for _ in range(40)))
    assert len(identity._jar.snapshot()) == 40


# --- a solver's own requests share the jar ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_solver_fetch_sends_and_stores_the_same_cookies() -> None:
    from eazy_sdk.clients.executor import _RuntimeFetch
    from eazy_sdk.response import NormalizedResponse

    sent: list[dict[str, str]] = []

    class Runtime:
        def send(self, prepared: Any, *, options: Any) -> NormalizedResponse[object]:
            sent.append({f.name.decode().lower(): f.value.decode() for f in prepared.headers})
            return NormalizedResponse(200, "", "GET", (("Set-Cookie", "cleared=1; Path=/"),), b"")

    class Transport:
        user_agent = None

    jar = CookieJar()
    jar.store(f"{AUTH}/", ["sid=s1; Path=/"])
    fetch = _RuntimeFetch(Runtime(), None, Transport(), None, (jar, Cookies()))  # type: ignore[arg-type]
    await fetch(f"{AUTH}/challenge")
    await fetch(f"{AUTH}/challenge", headers={"Cookie": "own=1"})

    assert sent[0]["cookie"] == "sid=s1"
    assert sent[1]["cookie"] == "own=1"
    assert jar.header_pairs(f"{AUTH}/") == (("sid", "s1"), ("cleared", "1"))


# --- what the declaration alone already proves wrong ------------------------------------------


def test_a_cookie_credential_and_a_cookie_session_are_not_declared_together() -> None:
    class Both(SyncApi):
        cookies = Cookies()
        security = CookieScheme("sid")

        echo = op(AuthEcho)

    with (
        _client(MailSite()) as client,
        pytest.raises(PlanError, match="a site is one or the other"),
    ):
        Both(client).echo()


def test_an_operation_of_a_cookie_site_does_not_write_the_cookie_header() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class ByHand(HttpOperation[Echo]):
        __http__ = Http.get("/echo")

        cookie: Header[str]

    class Api(SyncApi):
        cookies = Cookies()

        by_hand = op(ByHand)

    with _client(MailSite()) as client, pytest.raises(PlanError, match="writes the Cookie header"):
        Api(client).by_hand(cookie="sid=1")


def test_opting_out_needs_something_to_opt_out_of() -> None:
    class Api(SyncApi):
        outside = op(Outside)

    with _client(MailSite()) as client, pytest.raises(PlanError, match="nothing to opt out of"):
        Api(client).outside()


def test_cookies_given_to_an_identity_need_a_service_that_uses_them() -> None:
    saved = CookieState((StoredCookie("sid", "1", "mail.example"),))
    with _client(MailSite()) as client:
        with pytest.raises(PlanError, match="nowhere to go"):
            PlainApi(client, identity=Identity(cookies=saved))

        class PlainRoot(SyncRoot):
            plain = api_group(PlainApi)

        with pytest.raises(PlanError, match="nowhere to go"):
            PlainRoot(client, identity=Identity(cookies=saved))


def test_the_declaration_is_a_cookies_object() -> None:
    with pytest.raises(TypeError, match=r"cookies must be a Cookies"):

        class Wrong(SyncApi):
            cookies = True

    with pytest.raises(TypeError, match="CookieState"):
        Identity(cookies=[StoredCookie("sid", "1", "mail.example")])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ({"required": ("sid", "")}, ValueError, "empty string"),
        ({"required": ("sid", "sid")}, ValueError, "twice"),
        ({"required": "sid"}, TypeError, "tuple of cookie names"),
    ],
)
def test_required_names_are_checked_where_they_are_written(
    arguments: dict[str, Any], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        Cookies(**arguments)
