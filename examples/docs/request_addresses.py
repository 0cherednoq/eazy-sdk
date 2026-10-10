"""Name a neighbouring service in a request, and request a link a response carried."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, Inject, api_group, bind, op
from eazy_sdk.auth import Cookies
from eazy_sdk.cookies import cookies_of
from eazy_sdk.core.errors import PlanError
from eazy_sdk.dependencies import service_url
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import Form, Url, markers
from eazy_sdk.response import Bytes

MAIL = "https://e.mail.example"
ACCOUNT = "https://account.mail.example"
FILES = "https://files.mail.example"


@dataclass(slots=True)
class AddressSite:
    seen: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            self.seen.append(
                f"POST {request.url.host}/login origin={request.headers['origin']} "
                f"referer={request.headers['referer']}"
            )
            return httpx.Response(
                200,
                json={"attachment": f"{FILES}/get?id=7"},
                headers={"set-cookie": "sid=s1; Domain=.mail.example; Path=/"},
            )
        self.seen.append(f"GET {request.url.host}{request.url.path} cookie={request.headers.get('cookie', '')}")
        return httpx.Response(200, content=b"%PDF-1.7")


class SignedIn(BaseModel):
    attachment: str


class PagesApi(AsyncApi):
    """The host that shows the sign-in page; nothing is called on it here."""


# region docs: request-addresses
# examples/docs/request_addresses.py
@dataclass(frozen=True, slots=True, kw_only=True)
class SignIn(HttpOperation[SignedIn]):
    __http__ = Http.post(
        "/login",
        inject=(
            Inject(markers.Header("Origin"), service_url(PagesApi)),
            Inject(markers.Header("Referer"), service_url(PagesApi, "/login")),
        ),
    )

    username: Form[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class Download(HttpOperation[bytes]):
    __http__ = Http.get(markers.Url(hosts=("*.mail.example",)), success={200: Bytes()})

    url: Url[str]


class MailApi(AsyncApi):
    sign_in = op(SignIn)
    download = op(Download)


class MailSdk(AsyncRoot):
    cookies = Cookies()

    pages = api_group(PagesApi)
    mail = api_group(MailApi)
# endregion docs: request-addresses


async def main() -> None:
    site = AddressSite()
    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    sdk = MailSdk.from_handler(
        handler=AsyncHttpxHandler(raw, owns_client=True),
        base_url=MAIL,
        bindings=(bind(PagesApi, base_url=ACCOUNT),),
    )
    async with sdk:
        signed = await sdk.mail.sign_in(username="ada")
        body = await sdk.mail.download(url=signed.attachment)
        print("downloaded:", body.decode())
        print("cookies kept:", ", ".join(cookie.name for cookie in cookies_of(sdk)))
        try:
            await sdk.mail.download(url="https://elsewhere.example/get?id=7")
        except PlanError as error:
            print("refused:", str(error).split(": ", 1)[1])
    for line in site.seen:
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
