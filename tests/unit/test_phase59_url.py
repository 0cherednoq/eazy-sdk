"""Phase 59.6: the address of a request given by the caller.

The scenario is a mail attachment: the message names where the file is, on another host of the
same site, and the download is an ordinary operation. It carries the cookies the site set, and
a link that leads anywhere else is refused before a byte is sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import UNSET, AsyncApi, AsyncRoot, Http, HttpOperation, Omittable, api_group, op
from eazy_sdk.auth import Cookies
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import Path, Url, markers
from eazy_sdk.response import Bytes

MAIL = "https://e.mail.example"
FILES = "https://af.mail.example"


class Ok(BaseModel):
    ok: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class SignIn(HttpOperation[Ok]):
    __http__ = Http.post("/login")


@dataclass(frozen=True, slots=True, kw_only=True)
class Download(HttpOperation[bytes]):
    __http__ = Http.get(markers.Url(hosts=("*.mail.example",)), success={200: Bytes()})

    url: Url[str]
    disposition: Annotated[Omittable[str], markers.Query("d")] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadAsIs(HttpOperation[bytes]):
    __http__ = Http.get(markers.Url(hosts=("*.mail.example",)), success={200: Bytes()})

    url: Url[str]


class MailApi(AsyncApi):
    sign_in = op(SignIn)
    download = op(Download)
    download_as_is = op(DownloadAsIs)


class Sdk(AsyncRoot):
    cookies = Cookies()

    mail = api_group(MailApi)


def _sdk(seen: list[httpx.Request]) -> Sdk:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/login":
            return httpx.Response(
                200,
                json={"ok": True},
                headers={"set-cookie": "sid=s1; Domain=.mail.example; Path=/"},
            )
        if request.url.path == "/moved":
            return httpx.Response(302, headers={"location": f"{FILES}/getattach?id=7"})
        return httpx.Response(200, content=b"%PDF-1.7")

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Sdk.from_handler(handler=AsyncHttpxHandler(raw, owns_client=True), base_url=MAIL)


@pytest.mark.asyncio
async def test_a_link_from_a_response_is_requested_as_it_is_with_the_site_cookies() -> None:
    seen: list[httpx.Request] = []
    async with _sdk(seen) as sdk:
        await sdk.mail.sign_in()
        body = await sdk.mail.download_as_is(url=f"{FILES}/getattach?id=7&name=a%20b.pdf")

    request = seen[-1]
    assert body == b"%PDF-1.7"
    assert (request.url.host, request.url.path) == ("af.mail.example", "/getattach")
    assert dict(request.url.params) == {"id": "7", "name": "a b.pdf"}
    assert request.headers["cookie"] == "sid=s1"


@pytest.mark.asyncio
async def test_a_query_field_is_added_to_the_link() -> None:
    """An operation that declares query fields owns the query, and writes it in its own way."""

    seen: list[httpx.Request] = []
    async with _sdk(seen) as sdk:
        await sdk.mail.download(url=f"{FILES}/getattach?id=7&name=a%20b", disposition="inline")
    assert dict(seen[-1].url.params) == {"id": "7", "name": "a b", "d": "inline"}


@pytest.mark.asyncio
async def test_a_relative_link_is_resolved_against_the_service() -> None:
    seen: list[httpx.Request] = []
    async with _sdk(seen) as sdk:
        await sdk.mail.download(url="/getattach?id=7")
    assert str(seen[-1].url) == f"{MAIL}/getattach?id=7"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example/getattach?id=7",
        "https://mail.example.evil.test/x",
        "ftp://af.mail.example/x",
        "//evil.example/x",
    ],
)
async def test_a_link_to_a_host_not_listed_is_refused_before_the_request(link: str) -> None:
    seen: list[httpx.Request] = []
    async with _sdk(seen) as sdk:
        with pytest.raises(PlanError, match=r"Url\(hosts=\.\.\.\) lists only \*\.mail\.example"):
            await sdk.mail.download(url=link)
    assert seen == []


@pytest.mark.asyncio
async def test_prepare_and_send_take_the_same_address() -> None:
    seen: list[httpx.Request] = []
    async with _sdk(seen) as sdk:
        request = sdk.mail.download.request(url=f"{FILES}/getattach?id=9")
        assert await sdk.mail.download.send(request) == b"%PDF-1.7"
        envelope = await sdk.mail.download.with_response(url=f"{FILES}/getattach?id=9")
    assert envelope.response.url == f"{FILES}/getattach?id=9"
    assert [str(item.url) for item in seen] == [f"{FILES}/getattach?id=9"] * 2


def test_an_address_without_hosts_is_refused_at_declaration() -> None:
    with pytest.raises(PlanError, match="lists no hosts"):
        Http.get(markers.Url())
    with pytest.raises(ValueError, match="tuple of host patterns"):
        markers.Url(hosts="*.mail.example")  # type: ignore[arg-type]


def test_the_field_and_the_path_are_declared_together() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class NoField(HttpOperation[bytes]):
        __http__ = Http.get(markers.Url(hosts=("*.mail.example",)), success={200: Bytes()})

    @dataclass(frozen=True, slots=True, kw_only=True)
    class OwnPath(HttpOperation[bytes]):
        __http__ = Http.get("/files/{url}", success={200: Bytes()})

        url: Url[str]

    @dataclass(frozen=True, slots=True, kw_only=True)
    class OtherName(HttpOperation[bytes]):
        __http__ = Http.get(markers.Url(hosts=("*.mail.example",)), success={200: Bytes()})

        link: Url[str]

    @dataclass(frozen=True, slots=True, kw_only=True)
    class WithPath(HttpOperation[bytes]):
        __http__ = Http.get(markers.Url(hosts=("*.mail.example",)), success={200: Bytes()})

        url: Url[str]
        folder: Path[str]

    for operation, message in (
        (NoField, "do not match template"),
        (OwnPath, "declares a path of its own"),
        (OtherName, "do not match template"),
        (WithPath, "do not match template"),
    ):
        with pytest.raises(PlanError, match=message):
            op(operation).declaration  # noqa: B018
