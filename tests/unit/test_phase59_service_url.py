"""Phase 59.5: the address of a service as a source for ``Inject``.

The scenario is a sign-in form posted to one host with the ``Origin`` and ``Referer`` of the
page on another, and a field naming the mailbox on a third. The three addresses are the ones the
SDK root was assembled with, so moving a service moves everything that names it.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Inject, api_group, bind, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.dependencies import Injected, dependency, service_url
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import Body, BodyProjection, markers

MAIL = "https://e.mail.example"
ACCOUNT = "https://account.mail.example"
AUTH = "https://auth.mail.example"


class Ok(BaseModel):
    ok: bool = True


class _Form(BaseModel):
    login: str
    page: str


class PagesApi(AsyncApi):
    pass


class MailPagesApi(AsyncApi):
    pass


INBOX = dependency(str, name="inbox-address", provide=service_url(MailPagesApi, "/inbox"))


def _form(step: Submit, injected: Injected) -> _Form:
    return _Form(login=step.login, page=str(injected[INBOX.dependency]))


@dataclass(frozen=True, slots=True, kw_only=True)
class Submit(HttpOperation[Ok]):
    __http__ = Http.post(
        "/cgi-bin/auth",
        projection=BodyProjection(target=_Form, using=_form, encoding=Body.form()),
        requires=(INBOX,),
        inject=(
            Inject(markers.Header("Origin"), service_url(PagesApi)),
            Inject(markers.Header("Referer"), service_url(PagesApi, "/")),
            Inject(markers.Header("X-Self"), service_url()),
            Inject(markers.Query("back"), service_url(path="/done")),
        ),
    )

    login: str


class AuthApi(AsyncApi):
    submit = op(Submit)


class Sdk(AsyncRoot):
    pages = api_group(PagesApi)
    auth = api_group(AuthApi)
    mail = api_group(MailPagesApi)


def _site(seen: list[httpx.Request]) -> AsyncHttpxHandler:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return AsyncHttpxHandler(raw, owns_client=True)


def _sdk(seen: list[httpx.Request], *, account: str = ACCOUNT, mail: str = MAIL) -> Sdk:
    return Sdk.from_handler(
        handler=_site(seen),
        base_url=mail,
        bindings=(bind(PagesApi, base_url=account), bind(AuthApi, base_url=AUTH)),
    )


@pytest.mark.asyncio
async def test_headers_query_and_a_projected_field_name_the_bound_addresses() -> None:
    seen: list[httpx.Request] = []
    async with _sdk(seen) as sdk:
        await sdk.auth.submit(login="ada")

    request = seen[0]
    assert request.url.host == "auth.mail.example"
    assert request.headers["origin"] == ACCOUNT
    assert request.headers["referer"] == f"{ACCOUNT}/"
    assert request.headers["x-self"] == AUTH
    assert request.url.params["back"] == f"{AUTH}/done"
    form = parse_qs(request.content.decode())
    assert form == {"login": ["ada"], "page": [f"{MAIL}/inbox"]}


@pytest.mark.asyncio
async def test_moving_a_service_moves_everything_that_names_it() -> None:
    seen: list[httpx.Request] = []
    async with _sdk(seen, account="https://account.corp.test", mail="https://e.corp.test") as sdk:
        await sdk.auth.submit(login="ada")

    request = seen[0]
    assert request.headers["origin"] == "https://account.corp.test"
    assert request.headers["referer"] == "https://account.corp.test/"
    assert parse_qs(request.content.decode())["page"] == ["https://e.corp.test/inbox"]


@pytest.mark.asyncio
async def test_a_router_outside_the_root_is_refused_before_the_request() -> None:
    class Elsewhere(AsyncApi):
        pass

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Ping(HttpOperation[Ok]):
        __http__ = Http.get(
            "/ping", inject=(Inject(markers.Header("Origin"), service_url(Elsewhere)),)
        )

    class PingApi(AsyncApi):
        ping = op(Ping)

    class PingSdk(AsyncRoot):
        ping = api_group(PingApi)

    seen: list[httpx.Request] = []
    async with PingSdk.from_handler(handler=_site(seen), base_url=MAIL) as sdk:
        with pytest.raises(PlanError, match=r"service_url\(Elsewhere\) is not a router"):
            await sdk.ping.ping()
    assert seen == []


@pytest.mark.asyncio
async def test_a_base_class_shared_by_two_addresses_is_refused() -> None:
    class Family(AsyncApi):
        pass

    class First(Family):
        pass

    class Second(Family):
        pass

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Ping(HttpOperation[Ok]):
        __http__ = Http.get(
            "/ping", inject=(Inject(markers.Header("Origin"), service_url(Family)),)
        )

    class PingApi(AsyncApi):
        ping = op(Ping)

    class FamilySdk(AsyncRoot):
        first = api_group(First)
        second = api_group(Second)
        ping = api_group(PingApi)

    seen: list[httpx.Request] = []
    sdk = FamilySdk.from_handler(
        handler=_site(seen), base_url=MAIL, bindings=(bind(Second, base_url=AUTH),)
    )
    async with sdk:
        with pytest.raises(PlanError, match="names routers at several addresses"):
            await sdk.ping.ping()


def test_what_is_not_a_router_or_a_path_is_refused_at_declaration() -> None:
    with pytest.raises(PlanError, match="takes a router class"):
        service_url("PagesApi")  # type: ignore[arg-type]
    with pytest.raises(PlanError, match="starts with '/'"):
        service_url(PagesApi, "inbox")
