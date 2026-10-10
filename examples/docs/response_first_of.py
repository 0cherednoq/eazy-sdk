"""Read one field from the first of several places that has it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from eazy_sdk_html import Regex
from pydantic import BaseModel

from eazy_sdk import Client, Http, HttpOperation, Path, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import FromCookie, MalformedResponseError

BASE_URL = "https://account.example"
PAGES = {
    "/in-page": ('<script>{"csrf":"from-page"}</script>', "act=from-cookie; Path=/"),
    "/in-cookie": ("<html>no state here</html>", "act=from-cookie; Path=/"),
    "/nowhere": ("<html>no state here</html>", ""),
}


def _site(request: httpx.Request) -> httpx.Response:
    body, cookie = PAGES[request.url.path]
    headers = {"content-type": "text/html; charset=utf-8"}
    if cookie:
        headers["set-cookie"] = cookie
    return httpx.Response(200, headers=headers, content=body.encode())


# region docs: response-first-of
# examples/docs/response_first_of.py
class LoginPage(BaseModel):
    csrf: Annotated[str, Regex(r'"csrf":"([^"]+)"') | FromCookie("act")]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenLogin(HttpOperation[LoginPage]):
    __http__ = Http.get("/{page}")

    page: Path[str] = "in-page"
# endregion docs: response-first-of


class AccountApi(SyncApi):
    login = op(OpenLogin)


def main() -> None:
    raw = httpx.Client(transport=httpx.MockTransport(_site), headers={}, cookies={})
    with Client(base_url=BASE_URL, handler=HttpxHandler(raw, owns_client=True)) as client:
        api = AccountApi(client)
        print("page has it:", api.login(page="in-page").csrf)
        print("only the cookie has it:", api.login(page="in-cookie").csrf)
        try:
            api.login(page="nowhere")
        except MalformedResponseError as error:
            print("nowhere:", type(error).__name__)


if __name__ == "__main__":
    main()
