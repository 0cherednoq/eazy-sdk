"""Pydoll-specific API, capture, and ownership checks on Pydoll 3."""

# The optional backend must skip cleanly in a core-only developer environment.

from __future__ import annotations

import asyncio
import contextlib
import inspect
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from typing import cast

import pytest
from eazy_sdk_browser.errors import BrowserDeclarationError
from eazy_sdk_browser.handlers import CapturePolicy
from eazy_sdk_browser.locators import any_of, css

pytest.importorskip("pydoll")

from eazy_sdk_browser.handlers.pydoll import PYDOLL_PROFILE, PydollDriver
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.browser.tab import Tab
from pydoll.elements.web_element import WebElement
from pydoll.protocol.page.events import PageEvent

from eazy_sdk.handlers import CapabilityLevel, TransportError

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def origin(self) -> str:
        host, port = cast("tuple[str, int]", self.server_address)
        return f"http://{host}:{port}"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path == "/":
            body = b"<!doctype html><body><button id='ready'>ready</button></body>"
            self._send("text/html", body)
        elif self.path == "/binary":
            self._send("application/octet-stream", b"\x00\xffbinary\x80")
        elif self.path == "/large":
            self._send("application/json", b'"' + b"x" * 64 + b'"')
        elif self.path.startswith("/small"):
            self._send("application/json", b'{"ok":true}')
        elif self.path == "/image.png":
            self._send("image/png", b"\x89PNG" * 8)
        else:
            self._send("text/plain", b"not found", status=404)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, content_type: str, body: bytes, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        with contextlib.suppress(ConnectionError, OSError):
            self.wfile.write(body)


@contextlib.contextmanager
def _server() -> Iterator[_Server]:
    server = _Server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_pydoll_3_public_api_used_by_the_adapter() -> None:
    assert version("pydoll-python").split(".", 1)[0] == "3"
    tab_api = {
        "query",
        "find",
        "go_to",
        "current_url",
        "page_source",
        "execute_script",
        "on",
        "remove_callback",
        "enable_page_events",
        "enable_network_events",
        "get_network_response_body",
        "get_cookies",
        "set_cookies",
        "wait_for_network_idle",
    }
    element_api = {
        "query",
        "find",
        "iframe_context",
        "click",
        "hover",
        "clear",
        "type_text",
        "get_attribute",
        "text",
        "value",
        "is_visible",
        "execute_script",
    }
    assert all(inspect.getattr_static(Tab, name, None) is not None for name in tab_api)
    assert all(inspect.getattr_static(WebElement, name, None) is not None for name in element_api)


def test_core_import_does_not_load_optional_pydoll_backend() -> None:
    code = (
        "import sys, eazy_sdk_browser; assert not any(n.startswith('pydoll') for n in sys.modules)"
    )
    completed = subprocess.run(  # noqa: S603 -- fixed interpreter and constant probe
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr

    import eazy_sdk_browser
    import eazy_sdk_browser.handlers as handlers

    assert not hasattr(eazy_sdk_browser, "PydollDriver")
    assert not hasattr(handlers, "PydollDriver")


async def _evaluate(tab: Tab, expression: str) -> object:
    response = await tab.execute_script(expression, return_by_value=True, await_promise=True)
    return response["result"]["result"].get("value")


async def test_capture_budgets_callback_ownership_and_closed_tab() -> None:
    options = ChromiumOptions()  # type: ignore[no-untyped-call]
    options.headless = True
    browser = Chrome(options=options)
    tab = await browser.start()
    with _server() as server:
        await tab.go_to(server.origin, timeout=20)
        foreign_events: list[str] = []
        foreign_id = await tab.on(
            PageEvent.NAVIGATED_WITHIN_DOCUMENT,
            lambda event: foreign_events.append(str(event["params"]["url"])),
        )
        key = object()
        driver = PydollDriver(
            tab,
            context_key=key,
            capture=CapturePolicy(max_body_bytes=16, max_total_bytes=15),
        )
        try:
            ready = await driver.find(any_of("#missing-a", "#ready", "#missing-b", timeout=1))
            assert ready is not None and await ready.text() == "ready"
            assert driver.context_key() is key
            assert driver.profile == PYDOLL_PROFILE
            assert PYDOLL_PROFILE.session_state is CapabilityLevel.BEST_EFFORT
            assert PYDOLL_PROFILE.shadow_dom is CapabilityLevel.UNSUPPORTED

            with pytest.raises(BrowserDeclarationError, match="pydoll"):
                await driver.peek(css("div >> text=bad"))

            before = driver.mark()
            await _evaluate(tab, "fetch('/binary').then(r => r.arrayBuffer())")
            binary = await driver.wait_response("/binary", within=5, since=before)
            assert binary is not None and binary.body == b"\x00\xffbinary\x80"

            image_mark = driver.mark()
            await _evaluate(
                tab,
                "new Promise(done => { const i=new Image(); i.onload=i.onerror=done; i.src='/image.png'; })",
            )
            assert await driver.wait_response("/image.png", within=0.1, since=image_mark) is None
            assert driver.mark() == image_mark

            large_mark = driver.mark()
            await _evaluate(tab, "fetch('/large').then(r => r.text())")
            large = await driver.wait_response("/large", within=5, since=large_mark)
            assert large is not None and (large.body, large.body_dropped) == (b"", True)

            small_mark = driver.mark()
            await _evaluate(tab, "fetch('/small?one').then(r => r.text())")
            await driver.wait_response("one", within=5, since=small_mark)
            await _evaluate(tab, "fetch('/small?two').then(r => r.text())")
            await driver.wait_response("two", within=5, since=small_mark)
            assert await driver.wait_response("one", within=0, since=small_mark) is None
            assert await driver.wait_response("two", within=0, since=small_mark) is not None

            navigation_mark = driver.navigations()
            await driver.aclose()
            await driver.aclose()
            await _evaluate(tab, "location.hash = 'after-close'")
            await asyncio.sleep(0.1)
            assert foreign_events and foreign_events[-1].endswith("#after-close")
            assert driver.navigations() == navigation_mark
            assert not driver._pending_tasks
            assert await tab.current_url()

            await tab.close()  # type: ignore[no-untyped-call]
            closed = PydollDriver(tab, capture=None)
            with pytest.raises(TransportError):
                await closed.find(css("body", timeout=0.1))
            await closed.aclose()
        finally:
            with contextlib.suppress(Exception):
                await tab.remove_callback(foreign_id)
            await browser.stop()  # type: ignore[no-untyped-call]


async def test_any_of_uses_one_deadline() -> None:
    options = ChromiumOptions()  # type: ignore[no-untyped-call]
    options.headless = True
    browser = Chrome(options=options)
    tab = await browser.start()
    with _server() as server:
        await tab.go_to(server.origin, timeout=20)
        driver = PydollDriver(tab, capture=None)
        started = time.monotonic()
        try:
            assert await driver.find(any_of("#a", "#b", "#c", timeout=0.2)) is None
            assert time.monotonic() - started < 0.5
            assert driver.profile.network is CapabilityLevel.UNSUPPORTED
        finally:
            await driver.aclose()
            await browser.stop()  # type: ignore[no-untyped-call]
