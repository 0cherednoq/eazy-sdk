"""One live-browser contract suite shared by Playwright and native Pydoll."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import pytest
from eazy_sdk_browser.driver import Driver
from eazy_sdk_browser.fetch import FetchAware, PageRequest
from eazy_sdk_browser.handlers.capture import DEFAULT_CAPTURE, CapturePolicy
from eazy_sdk_browser.handlers.playwright import PlaywrightDriver
from eazy_sdk_browser.handlers.pydoll import PydollDriver
from eazy_sdk_browser.locators import any_of, css, last
from eazy_sdk_browser.navigation import NavigationAware
from eazy_sdk_browser.network import NetworkAware
from eazy_sdk_browser.rich_text import RichTextAware
from eazy_sdk_browser.state import BrowserCookie, StateAware

pytestmark = [pytest.mark.integration, pytest.mark.contract, pytest.mark.timeout(120)]


class _Site(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.received_posts: list[tuple[str, str, bytes]] = []

    @property
    def origin(self) -> str:
        host, port = cast("tuple[str, int]", self.server_address)
        return f"http://{host}:{port}"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Site

    def do_GET(self) -> None:
        if self.path.startswith("/page"):
            self._send("text/html; charset=utf-8", _page(self.server.origin).encode())
        elif self.path == "/capture":
            self._send(
                "text/html; charset=utf-8",
                b"<button id='image'>image</button><script>"
                b"image.onclick=()=>{const i=new Image();i.src='/image.png'}</script>",
            )
        elif self.path == "/frame":
            self._send("text/html; charset=utf-8", _frame(self.server.origin).encode())
        elif self.path == "/inner":
            self._send("text/html; charset=utf-8", b'<button id="deep">Deep frame</button>')
        else:
            self._serve_api()

    def _serve_api(self) -> None:
        if self.path.startswith("/api/json"):
            self._send("application/json", b'{"answer":42}')
        elif self.path == "/redirect":
            self._send("text/plain", b"", status=302, headers=(("Location", "/api/json"),))
        elif self.path == "/api/binary":
            self._send("application/octet-stream", b"\x00\xffbinary\x80")
        elif self.path == "/api/large":
            self._send("application/json", b'"' + b"x" * 64 + b'"')
        elif self.path.startswith("/api/small"):
            self._send("application/json", b'{"ok":true}')
        elif self.path == "/image.png":
            self._send("image/png", b"\x89PNG" * 8)
        else:
            self._send("text/plain", b"not found", status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.received_posts.append(
            (self.headers.get("X-Contract", ""), self.headers.get("Cookie", ""), body)
        )
        self._send("application/octet-stream", body)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(
        self,
        content_type: str,
        body: bytes,
        *,
        status: int = 200,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        with contextlib.suppress(ConnectionError, OSError):
            self.wfile.write(body)


def _page(origin: str) -> str:
    return f"""<!doctype html><html><body>
<button id="fallback">Fallback</button><button id="preferred">Preferred</button>
<div class="row" data-n="1"> First row </div><div class="row" hidden>Hidden</div>
<div class="row" data-n="2">Second row</div><div id="quoted">Say "hello"</div>
<input id="name"><input id="agree" type="checkbox">
<select id="role"><option value="user">User</option><option value="admin">Admin</option></select>
<button id="hover">Hover</button><div id="tip" hidden>Tip</div>
<input id="key"><output id="keys">0</output>
<div id="editor" contenteditable="true" data-events=""></div>
<button id="storage">Storage</button><button id="request">Request</button>
<iframe id="outer" src="{origin}/frame"></iframe>
<script>
hover.onmouseenter=()=>tip.hidden=false;
key.onkeydown=()=>keys.value=String(Number(keys.value)+1);
for (const event of ['input','change']) agree.addEventListener(event,()=>{{
  agree.dataset.events = (agree.dataset.events ? agree.dataset.events + ',' : '') + event;
}});
agree.addEventListener('change',()=>agree.dataset.checked=String(agree.checked));
for (const event of ['input','change']) role.addEventListener(event,()=>{{
  role.dataset.events = (role.dataset.events ? role.dataset.events + ',' : '') + event;
}});
for (const event of ['input','change']) editor.addEventListener(event,()=>{{
  editor.dataset.events += (editor.dataset.events ? ',' : '') + event;
}});
storage.onclick=()=>localStorage.setItem('token','saved');
request.onclick=()=>{{ fetch('/api/json'); fetch('/api/binary'); }};
</script></body></html>"""


def _frame(origin: str) -> str:
    # localhost versus 127.0.0.1 makes the inner frame cross-origin while using
    # the same deterministic local server.
    foreign = origin.replace("127.0.0.1", "localhost")
    return f'<!doctype html><html><body><iframe id="inner" src="{foreign}/inner"></iframe></body></html>'


@contextlib.contextmanager
def _site() -> Iterator[_Site]:
    server = _Site()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@asynccontextmanager
async def _driver(
    backend: str,
    *,
    context_key: object | None = None,
    capture: CapturePolicy | None = DEFAULT_CAPTURE,
) -> AsyncIterator[Driver]:
    if backend == "playwright":
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            playwright_browser = await playwright.chromium.launch()
            page = await playwright_browser.new_page()
            playwright_driver = PlaywrightDriver(page, capture=capture)
            try:
                yield playwright_driver
            finally:
                await playwright_driver.aclose()
                await playwright_browser.close()
        return

    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

    options = ChromiumOptions()  # type: ignore[no-untyped-call]
    options.headless = True
    # A shared CI runner can take longer than Pydoll's default 10 s to bring Chrome up.
    options.start_timeout = 30
    pydoll_browser = Chrome(options=options)
    tab = await pydoll_browser.start()
    pydoll_driver = PydollDriver(tab, context_key=context_key, capture=capture)
    try:
        yield pydoll_driver
    finally:
        await pydoll_driver.aclose()
        await pydoll_browser.stop()  # type: ignore[no-untyped-call]


@pytest.mark.parametrize("backend", ["playwright", "pydoll"])
async def test_live_driver_contract(backend: str) -> None:
    with _site() as site:
        key = object()
        async with _driver(backend, context_key=key) as driver:
            assert isinstance(driver, Driver)
            assert isinstance(driver, NavigationAware)
            assert isinstance(driver, NetworkAware)
            assert isinstance(driver, StateAware)
            assert isinstance(driver, FetchAware)
            assert isinstance(driver, RichTextAware)
            if backend == "pydoll":
                assert driver.context_key() is key
            else:
                assert driver.context_key() is not None

            await driver.goto(f"{site.origin}/page", wait="load", within=20)
            preferred = await driver.find(any_of("#preferred", "#fallback", timeout=2))
            xpath = await driver.find(css("//*[@id='preferred']", timeout=2))
            exact = await driver.find(css(r'text="Say \"hello\""', timeout=2))
            contains = await driver.find(css('div:has-text("Second row")', timeout=2))
            rows = await driver.peek_all(css(".row"))
            oldest = await driver.peek(css(".row"))
            newest = await driver.peek(last(".row"))

            assert preferred is not None and await preferred.text() == "Preferred"
            assert xpath is not None and await xpath.text() == "Preferred"
            assert exact is not None and await exact.text() == 'Say "hello"'
            assert contains is not None and await contains.attribute("data-n") == "2"
            assert [await row.attribute("data-n") for row in rows] == ["1", "2"]
            assert oldest is not None and await oldest.attribute("data-n") == "1"
            assert newest is not None and await newest.attribute("data-n") == "2"

            name = await driver.find(css("#name", timeout=2))
            agree = await driver.find(css("#agree", timeout=2))
            role = await driver.find(css("#role", timeout=2))
            hover = await driver.find(css("#hover", timeout=2))
            key = await driver.find(css("#key", timeout=2))
            assert all(item is not None for item in (name, agree, role, hover, key))
            assert name is not None and agree is not None and role is not None
            assert hover is not None and key is not None
            await name.fill("Ada")
            await agree.check()
            await agree.check()
            assert await agree.attribute("data-checked") == "true"
            assert await agree.attribute("data-events") == "input,change"
            await role.select("Admin")
            assert await role.attribute("data-events") == "input,change"
            await hover.hover()
            await key.press("Enter")
            assert await name.value() == "Ada"
            assert await agree.value() == "on"
            await agree.uncheck()
            assert await agree.attribute("data-checked") == "false"
            assert await agree.attribute("data-events") == "input,change,input,change"
            assert await role.value() == "admin"
            tip = await driver.find(css("#tip", timeout=2))
            keys = await driver.find(css("#keys", timeout=2))
            assert tip is not None and await tip.visible()
            assert keys is not None and await keys.text() == "1"

            assert await driver.set_html(css("#editor", timeout=2), "<b>rich</b>")
            editor = await driver.find(css("#editor", timeout=2))
            assert editor is not None
            assert await editor.text() == "rich"
            assert await editor.attribute("data-events") == "input,change"

            deep = await driver.find(css("#deep", timeout=5, frames=("#outer", "#inner")))
            assert deep is not None and await deep.text() == "Deep frame"
            frame_reply = await driver.fetch(
                PageRequest("GET", f"{site.origin}/api/json"),
                frames=("#outer", "#inner"),
            )
            assert (frame_reply.status, json.loads(frame_reply.body)) == (200, {"answer": 42})

            storage = await driver.find(css("#storage", timeout=2))
            assert storage is not None
            await storage.click()
            state = await driver.export_state()
            assert state.origins and state.origins[0].mapping()["token"] == "saved"
            expires = datetime.now(UTC) + timedelta(hours=1)
            await driver.add_cookies(
                (
                    BrowserCookie(
                        "contract",
                        "yes",
                        domain="127.0.0.1",
                        path="/",
                        expires_at=expires,
                        http_only=True,
                        same_site="Lax",
                    ),
                )
            )
            saved = next(
                cookie
                for cookie in (await driver.export_state()).cookies
                if cookie.name == "contract"
            )
            assert (saved.domain, saved.path, saved.http_only, saved.same_site) == (
                "127.0.0.1",
                "/",
                True,
                "Lax",
            )
            assert saved.expires_at is not None
            assert abs((saved.expires_at - expires).total_seconds()) < 2

            reply = await driver.fetch(
                PageRequest(
                    "POST",
                    f"{site.origin}/echo",
                    headers=(("X-Contract", "preserved"),),
                    body=b"\x00request\xff",
                    credentials="include",
                )
            )
            assert (reply.status, reply.body) == (200, b"\x00request\xff")
            assert site.received_posts[-1] == (
                "preserved",
                "contract=yes",
                b"\x00request\xff",
            )
            redirected = await driver.fetch(PageRequest("GET", f"{site.origin}/redirect"))
            assert redirected.redirected and redirected.url.endswith("/api/json")
            assert json.loads(redirected.body) == {"answer": 42}

            mark = driver.mark()
            request = await driver.find(css("#request", timeout=2))
            assert request is not None
            await request.click()
            binary, payload = await asyncio.gather(
                driver.wait_response("/api/binary", within=5, since=mark),
                driver.wait_response("/api/json", within=5, since=mark),
            )
            assert binary is not None and binary.body == b"\x00\xffbinary\x80"
            assert payload is not None and json.loads(payload.body) == {"answer": 42}

            await driver.goto(f"{site.origin}/page?idle", wait="networkidle", within=20)
            before = driver.navigations()
            await driver.goto(f"{site.origin}/page?idle#next", wait="domcontentloaded", within=20)
            assert driver.navigations() > before


@pytest.mark.parametrize("backend", ["playwright", "pydoll"])
async def test_candidates_share_one_deadline(backend: str) -> None:
    with _site() as site:
        async with _driver(backend, capture=None) as driver:
            await driver.goto(f"{site.origin}/capture", wait="load", within=20)
            started = asyncio.get_running_loop().time()
            assert await driver.find(any_of("#a", "#b", "#c", timeout=0.2)) is None
            assert asyncio.get_running_loop().time() - started < 0.5
            started = asyncio.get_running_loop().time()
            assert await driver.peek(any_of("#a", "#b", "#c", timeout=5)) is None
            assert asyncio.get_running_loop().time() - started < 0.5


@pytest.mark.parametrize("backend", ["playwright", "pydoll"])
async def test_capture_policy_is_shared_by_both_backends(backend: str) -> None:
    policy = CapturePolicy(max_body_bytes=16, max_total_bytes=15)
    with _site() as site:
        async with _driver(backend, capture=policy) as driver:
            assert isinstance(driver, NetworkAware)
            assert isinstance(driver, FetchAware)
            await driver.goto(f"{site.origin}/capture", wait="networkidle", within=20)
            baseline = driver.mark()

            image = await driver.find(css("#image", timeout=2))
            assert image is not None
            await image.click()
            await asyncio.sleep(0.25)
            assert driver.mark() == baseline

            await driver.fetch(PageRequest("GET", f"{site.origin}/api/binary"))
            binary = await driver.wait_response("/api/binary", within=5, since=baseline)
            assert binary is not None and binary.body == b"\x00\xffbinary\x80"

            await driver.fetch(PageRequest("GET", f"{site.origin}/api/large"))
            large = await driver.wait_response("/api/large", within=5, since=baseline)
            assert large is not None and (large.body, large.body_dropped) == (b"", True)

            await driver.fetch(PageRequest("GET", f"{site.origin}/api/small?last"))
            small = await driver.wait_response("last", within=5, since=baseline)
            assert small is not None and small.body == b'{"ok":true}'
            assert await driver.wait_response("/api/binary", within=0, since=baseline) is None


async def _assert_close_contract(
    first: PlaywrightDriver | PydollDriver,
    second: PlaywrightDriver | PydollDriver,
    url: str,
    alive: Callable[[], Awaitable[str]],
) -> None:
    await first.goto(url, wait="load", within=20)
    await second.find(css("body", timeout=2))
    await first.aclose()
    await first.aclose()
    closed_mark = first.mark()
    live_mark = second.mark()

    await second.fetch(PageRequest("GET", f"{url.rsplit('/', 1)[0]}/api/small?close"))
    reply = await second.wait_response("close", within=5, since=live_mark)
    assert reply is not None and reply.body == b'{"ok":true}'
    assert first.mark() == closed_mark

    await second.aclose()
    await second.aclose()
    assert await alive()


async def _playwright_close_contract(url: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()

        async def alive() -> str:
            return page.url

        try:
            await _assert_close_contract(PlaywrightDriver(page), PlaywrightDriver(page), url, alive)
        finally:
            await browser.close()


async def _pydoll_close_contract(url: str) -> None:
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

    options = ChromiumOptions()  # type: ignore[no-untyped-call]
    options.headless = True
    # A shared CI runner can take longer than Pydoll's default 10 s to bring Chrome up.
    options.start_timeout = 30
    browser = Chrome(options=options)
    tab = await browser.start()

    async def alive() -> str:
        return str(await tab.current_url())

    try:
        await _assert_close_contract(PydollDriver(tab), PydollDriver(tab), url, alive)
    finally:
        await browser.stop()  # type: ignore[no-untyped-call]


@pytest.mark.parametrize("backend", ["playwright", "pydoll"])
async def test_close_is_idempotent_and_does_not_close_the_page(backend: str) -> None:
    with _site() as site:
        url = f"{site.origin}/capture"
        if backend == "playwright":
            await _playwright_close_contract(url)
        else:
            await _pydoll_close_contract(url)
