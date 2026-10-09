"""Local webmail that reports a login outcome in a redirect and keeps its token in the page."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

import httpx

from eazy_sdk import AsyncClient, Client
from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler

BASE_URL = "https://mail.example"
MAILBOX = "ada@mail.example"
PASSWORD = "mail-password"  # noqa: S105 - the teaching site's only account
SESSION_ID = "sid-42"
PAGE_TOKEN = "page-token-42"  # noqa: S105 - printed by the examples on purpose
DEVICE_KEY = "device-42"

MAIL_PAGE = f"""<!doctype html>
<html><head><title>Mail</title></head><body>
<script>
  window.boot = {{"ads":{{"token":"ads-token","email":"ads@mail.example"}},
    "account":{{"token":"{PAGE_TOKEN}","email":"{MAILBOX}"}}}};
</script>
<main><h1>Inbox</h1></main>
</body></html>
"""

LOGIN_PAGE = """<!doctype html>
<html><head><title>Sign in</title></head><body><form action="/cgi-bin/auth"></form></body></html>
"""


@dataclass(slots=True)
class RedirectSite:
    """Answers the login with ``302`` and nothing else: the target is the whole outcome."""

    calls: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        path = request.url.path
        if path == "/cgi-bin/auth":
            return self._login(request)
        if path == "/inbox/":
            return self._inbox(request)
        if path == "/api/folders":
            return self._folders(request)
        if path == "/api/devices":
            return self._devices(request)
        return httpx.Response(404, json={"error": "route_not_found"})

    def _login(self, request: httpx.Request) -> httpx.Response:
        form = {name: values[0] for name, values in parse_qs(request.content.decode()).items()}
        username, password = form.get("username", ""), form.get("password", "")
        if username == "banned@mail.example":
            return _redirect("/login?errno=25")
        if username == "otp@mail.example":
            return _redirect("/cgi-bin/secstep?otp=1")
        if (username, password) != (MAILBOX, PASSWORD):
            return _redirect("/login?fail=1")
        return _redirect(f"{BASE_URL}/inbox/", cookie=f"sid={SESSION_ID}; Path=/; HttpOnly")

    def _inbox(self, request: httpx.Request) -> httpx.Response:
        signed_in = _cookies(request).get("sid") == SESSION_ID
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=(MAIL_PAGE if signed_in else LOGIN_PAGE).encode(),
        )

    def _folders(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params
        accepted = (
            query.get("token") == PAGE_TOKEN
            and query.get("email") == MAILBOX
            and _cookies(request).get("sid") == SESSION_ID
        )
        if not accepted:
            return httpx.Response(401, json={"folders": []})
        return httpx.Response(200, json={"folders": ["Inbox", "Sent"]})

    def _devices(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params
        accepted = (
            query.get("token") == PAGE_TOKEN
            and query.get("email") == MAILBOX
            and request.headers.get("x-device") == DEVICE_KEY
        )
        if not accepted:
            return httpx.Response(401, json={"folders": []})
        return httpx.Response(200, json={"folders": ["Inbox", "Sent"]})


def _redirect(location: str, *, cookie: str | None = None) -> httpx.Response:
    headers = {"location": location, "content-type": "text/html; charset=utf-8"}
    if cookie is not None:
        headers["set-cookie"] = cookie
    return httpx.Response(302, headers=headers, content=b"<html>Redirecting</html>")


def _cookies(request: httpx.Request) -> dict[str, str]:
    parsed = SimpleCookie(request.headers.get("cookie", ""))
    return {name: morsel.value for name, morsel in parsed.items()}


@contextmanager
def redirect_client(site: RedirectSite | None = None) -> Iterator[Client]:
    raw = httpx.Client(
        transport=httpx.MockTransport(site or RedirectSite()), headers={}, cookies={}
    )
    with Client(base_url=BASE_URL, handler=HttpxHandler(raw, owns_client=True)) as client:
        yield client


@asynccontextmanager
async def async_redirect_client(site: RedirectSite | None = None) -> AsyncIterator[AsyncClient]:
    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(site or RedirectSite()), headers={}, cookies={}
    )
    async with AsyncClient(
        base_url=BASE_URL, handler=AsyncHttpxHandler(raw, owns_client=True)
    ) as client:
        yield client
