"""Phase 59.4: a field read from the first of several places that has it.

The scenario is a sign-in page whose form token sits in the page for one client and in a cookie
for another. One field names both sources, and the model needs no helper property and no
placeholder condition to tell the page from any other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
import pytest
from eazy_sdk_html import CSS, Regex, XPath
from pydantic import BaseModel

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.models import default_model_adapters
from eazy_sdk.response import (
    FirstOf,
    FromCookie,
    FromHeader,
    HeaderModel,
    Location,
    MalformedResponseError,
    UnexpectedResponseError,
)
from eazy_sdk.response.sources import is_header_model
from eazy_sdk.serialization import BackendCapabilityError

BASE = "https://account.example"
HTML = {"content-type": "text/html; charset=utf-8"}


def _serve(response: httpx.Response) -> Client:
    raw = httpx.Client(
        transport=httpx.MockTransport(lambda _request: response), headers={}, cookies={}
    )
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True))


class LoginPage(BaseModel):
    csrf: Annotated[str, Regex(r'"csrf":"([^"]+)"') | FromCookie("act")]


class CookieFirst(BaseModel):
    csrf: Annotated[str, FromCookie("act") | Regex(r'"csrf":"([^"]+)"')]


class ThreeWays(BaseModel):
    csrf: Annotated[
        str, CSS("input[name=csrf]::attr(value)") | XPath("//meta[@name='csrf']/@content")
    ]
    hint: Annotated[str | None, FromHeader("X-Hint") | FromCookie("hint")] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenLogin(HttpOperation[LoginPage]):
    __http__ = Http.get("/login")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenCookieFirst(HttpOperation[CookieFirst]):
    __http__ = Http.get("/login")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenThreeWays(HttpOperation[ThreeWays]):
    __http__ = Http.get("/login")


class Account(SyncApi):
    login = op(OpenLogin)
    cookie_first = op(OpenCookieFirst)
    three_ways = op(OpenThreeWays)


def _page(body: str, *cookies: str, **headers: str) -> httpx.Response:
    lines = [*HTML.items(), *headers.items(), *(("set-cookie", cookie) for cookie in cookies)]
    return httpx.Response(200, headers=lines, content=body.encode())


def test_the_value_is_taken_from_the_page_when_the_page_has_it() -> None:
    with _serve(_page('<script>{"csrf":"in-page"}</script>', "act=in-cookie")) as client:
        assert Account(client).login().csrf == "in-page"


def test_the_next_source_answers_when_the_first_does_not() -> None:
    with _serve(_page("<html>no state</html>", "act=in-cookie; Path=/")) as client:
        assert Account(client).login().csrf == "in-cookie"


def test_the_order_written_is_the_order_tried() -> None:
    with _serve(_page('<script>{"csrf":"in-page"}</script>', "act=in-cookie")) as client:
        assert Account(client).cookie_first().csrf == "in-cookie"
    with _serve(_page('<script>{"csrf":"in-page"}</script>')) as client:
        assert Account(client).cookie_first().csrf == "in-page"


def test_a_required_field_found_nowhere_is_a_malformed_response() -> None:
    with _serve(_page("<html>no state</html>")) as client, pytest.raises(
        MalformedResponseError
    ) as raised:
        Account(client).login()
    assert "found in none of its sources" in str(raised.value.__cause__)


def test_two_selectors_are_tried_in_order_and_an_optional_chain_may_stay_empty() -> None:
    with _serve(_page('<input name="csrf" value="from-input">')) as client:
        page = Account(client).three_ways()
    assert (page.csrf, page.hint) == ("from-input", None)

    second = _page('<meta name="csrf" content="from-meta">', "hint=cookie", **{"X-Hint": "header"})
    with _serve(second) as client:
        page = Account(client).three_ways()
    assert (page.csrf, page.hint) == ("from-meta", "header")


def test_a_chain_with_a_location_parameter_states_which_response_this_is() -> None:
    @dataclass(frozen=True, slots=True)
    class Refused:
        reason: Annotated[str, Location.query("fail") | FromHeader("X-Fail")]

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Submit(HttpOperation[Refused]):
        __http__ = Http.post("/auth", success={302: HeaderModel(Refused, when=Location())})

    class Api(SyncApi):
        submit = op(Submit)

    to_fail = httpx.Response(302, headers={"location": "/login?fail=1"})
    with _serve(to_fail) as client:
        assert Api(client).submit().reason == "1"

    by_header = httpx.Response(302, headers={"location": "/login", "x-fail": "header"})
    with _serve(by_header) as client:
        assert Api(client).submit().reason == "header"

    elsewhere = httpx.Response(302, headers={"location": "/inbox"})
    with _serve(elsewhere) as client, pytest.raises(UnexpectedResponseError):
        Api(client).submit()


def test_a_chain_around_the_body_keeps_the_model_a_header_model() -> None:
    class Around(BaseModel):
        token: Annotated[str, FromHeader("X-Token") | FromCookie("token")]

    models = default_model_adapters()
    assert is_header_model(Around, models)
    assert not is_header_model(LoginPage, models)


def test_a_chain_is_built_flat_and_compares_by_value() -> None:
    chain = Regex("a") | FromCookie("b") | FromHeader("c")
    assert chain == FirstOf(Regex("a"), FromCookie("b"), FromHeader("c"))
    assert len(chain.sources) == 3
    assert repr(chain).count(" | ") == 2
    assert not chain.states_the_response
    assert (Location.query("x") | FromCookie("b")).states_the_response


def test_what_cannot_be_a_source_is_refused() -> None:
    with pytest.raises(PlanError, match="stands on a field alone"):
        FromCookie("act") | Location(path="/login*")
    with pytest.raises(PlanError, match="at least two sources"):
        FirstOf(FromCookie("act"))
    with pytest.raises(PlanError, match="cannot be one of several sources"):
        FromCookie("act") | "act"


def test_sources_named_twice_on_one_field_are_refused() -> None:
    class Twice(BaseModel):
        csrf: Annotated[str, Regex("a(b)") | FromCookie("act"), CSS("b::text")]

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Open(HttpOperation[Twice]):
        __http__ = Http.get("/login")

    class Api(SyncApi):
        open = op(Open)

    with _serve(_page("<b>x</b>")) as client, pytest.raises(BackendCapabilityError, match="twice"):
        Api(client).open()
