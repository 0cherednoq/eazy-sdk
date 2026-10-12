"""The browser recorder, driven on a local site by a real Chrome.

Skipped where Pydoll or Chrome is not installed: the recorder is the one script of the skill
that needs a browser.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from collections.abc import Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import capture_inventory
import capture_normalize
import capture_redact
import pytest

pytest.importorskip("pydoll")

import capture_record

CHROME = (
    shutil.which("chrome")
    or shutil.which("google-chrome")
    or shutil.which("chromium")
    or next(
        (
            str(path)
            for path in (
                Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            )
            if path.exists()
        ),
        None,
    )
)
pytestmark = pytest.mark.skipif(CHROME is None, reason="Chrome is not installed")

PASSWORD = "Qw3rty-zebra-91"
TOKEN = "tok7c1e9a3b5d2f4a6c8e0b1d3f5a7c9e2"
LOGIN_PAGE = """<!doctype html><html><head><title>Sign in</title></head><body>
<form action="/auth" method="post">
<label>Login <input name="login" id="login"></label>
<label>Password <input name="password" id="password" type="password"></label>
<input type="hidden" name="csrf" value="c5f3a9d07b1e4c2f8a6d0e9b3f7c1a55">
<button id="enter" type="submit">Sign in</button></form></body></html>"""
INBOX_PAGE = f"""<!doctype html><html><head><title>Inbox</title></head><body>
<button id="more">Load more</button><ul id="list"></ul>
<script>
localStorage.setItem('device', 'dev-4f8a2c6e0b9d7f5a3c1e');
const boot = {{"token": "{TOKEN}"}};
const load = offset => fetch('/api/threads?offset=' + offset + '&token=' + boot.token)
  .then(r => r.json()).then(d => {{ document.getElementById('list').append(d.body.total); }});
document.getElementById('more').addEventListener('click', () => load(2));
load(0);
</script></body></html>"""


class _Site(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return

    def _send(
        self, status: int, kind: str, body: bytes, extra: tuple[tuple[str, str], ...] = ()
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/stub":
            # A page that leaves at once: its body is gone before anyone can ask for it.
            stub = (
                "<html><head><meta http-equiv='refresh' content=\"0;URL='/login'\"/></head></html>"
            )
            self._send(200, "text/html; charset=utf-8", stub.encode())
        elif path == "/login":
            self._send(200, "text/html; charset=utf-8", LOGIN_PAGE.encode())
        elif path == "/inbox/":
            self._send(200, "text/html; charset=utf-8", INBOX_PAGE.encode())
        elif path == "/api/threads":
            body = json.dumps({"status": 200, "body": {"total": 5, "threads": [{"id": "1"}]}})
            self._send(200, "application/json", body.encode())
        else:
            self._send(404, "text/plain", b"no")

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        cookies = (
            ("Set-Cookie", "sid=s9x8w7v6u5t4s3r2q1p0; Path=/; HttpOnly"),
            ("Set-Cookie", "seen=1; Path=/"),
        )
        self._send(302, "text/html", b"", (("Location", "/inbox/"), *cookies))


@pytest.fixture
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


async def _visitor(tab: Any, mark: Callable[[str], Awaitable[None]]) -> None:
    """Sign in and load one more page, the way a person would."""
    await mark("login")
    login = await tab.find(id="login", timeout=10)
    await login.click()
    await login.type_text("alice.reader@mailbox.example")
    password = await tab.find(id="password")
    await password.click()
    await password.type_text(PASSWORD)
    await (await tab.find(id="enter")).click()
    more = await tab.find(id="more", timeout=10)
    await asyncio.sleep(0.6)
    await mark("more threads")
    await more.click()
    await asyncio.sleep(0.8)


@pytest.fixture
def recording(site: str, tmp_path: Path) -> Path:
    options = capture_record.Options(
        url=f"{site}/login", out=tmp_path / "captures", chrome=CHROME, headless=True
    )
    return asyncio.run(capture_record.record(options, _visitor))


def _timeline(recording: Path) -> list[dict[str, Any]]:
    lines = (recording / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_recording_holds_traffic_actions_and_marks(recording: Path) -> None:
    capture = capture_normalize.merge([capture_normalize.read_any(recording)])
    assert capture.source == "recording"
    api = [e for e in capture.exchanges if e.path in {"/login", "/auth", "/inbox/", "/api/threads"}]
    assert [(e.method, e.path, e.status) for e in api] == [
        ("GET", "/login", 200),
        ("POST", "/auth", 302),
        ("GET", "/inbox/", 200),
        ("GET", "/api/threads", 200),
        ("GET", "/api/threads", 200),
    ]
    auth = api[1]
    # Exact wire facts: the form body, both cookies as separate headers, the browser's own headers.
    assert b"login=alice.reader%40mailbox.example" in auth.request_body
    assert auth.request_header("origin") is not None
    assert len([v for k, v in auth.response_headers if k.lower() == "set-cookie"]) == 2
    assert api[2].request_header("cookie") is not None
    assert b"Inbox" in api[2].response_body
    assert json.loads(api[3].response_body)["body"]["total"] == 5

    kinds = [event["kind"] for event in capture.timeline]
    assert kinds.count("mark") == 2
    actions = [event for event in capture.timeline if event["kind"] == "action"]
    described = [(a["action"], a["target"].get("id") or a["target"].get("tag")) for a in actions]
    assert ("input", "login") in described
    assert ("input", "password") in described
    assert ("click", "enter") in described
    assert ("submit", "form") in described
    assert ("click", "more") in described
    # The click that navigated away is not lost with its document.
    assert described.index(("click", "enter")) < described.index(("click", "more"))


def test_a_page_that_redirects_at_once_keeps_its_body(site: str, tmp_path: Path) -> None:
    async def visitor(tab: Any, mark: Callable[[str], Awaitable[None]]) -> None:
        await tab.find(id="login", timeout=10)
        await asyncio.sleep(0.3)

    options = capture_record.Options(
        url=f"{site}/stub", out=tmp_path / "captures", chrome=CHROME, headless=True
    )
    directory = asyncio.run(capture_record.record(options, visitor))
    capture = capture_normalize.merge([capture_normalize.read_any(directory)])
    stub = next(e for e in capture.exchanges if e.path == "/stub")
    assert b"http-equiv='refresh'" in stub.response_body
    assert next(e for e in capture.exchanges if e.path == "/login").response_body


def test_typed_values_stay_out_of_the_timeline(recording: Path) -> None:
    text = (recording / "timeline.jsonl").read_text(encoding="utf-8")
    assert PASSWORD not in text
    typed = json.loads((recording / "secrets.local.json").read_text(encoding="utf-8"))["typed"]
    assert {
        "value": PASSWORD,
        "field": {"type": "password", "name": "password", "label": "Password", "id": "password"},
    } in typed
    password = next(
        event
        for event in _timeline(recording)
        if event["kind"] == "action"
        and event.get("action") == "input"
        and event["target"]["id"] == "password"
    )
    assert password["length"] == len(PASSWORD)
    assert typed[password["value_ref"]]["value"] == PASSWORD


def test_requests_are_tied_to_the_action_that_caused_them(recording: Path) -> None:
    capture = capture_normalize.merge([capture_normalize.read_any(recording)])
    auth = next(e for e in capture.exchanges if e.path == "/auth")
    assert auth.meta["action"] == "submit /auth"
    assert auth.meta["resourceType"] == "Document"
    second = [e for e in capture.exchanges if e.path == "/api/threads"][1]
    assert second.meta["action"] == "click Load more"
    assert second.meta["initiator"] == "script"


def test_the_page_sees_no_listener_and_no_binding(site: str, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def visitor(tab: Any, mark: Callable[[str], Awaitable[None]]) -> None:
        await tab.find(id="login", timeout=10)
        for name in ("__eazy", capture_record._BINDING):
            result = await tab.execute_script(f"return typeof window[{name!r}]")
            seen[name] = result["result"]["result"]["value"]

    options = capture_record.Options(
        url=f"{site}/login", out=tmp_path / "captures", chrome=CHROME, headless=True
    )
    directory = asyncio.run(capture_record.record(options, visitor))
    assert seen == {"__eazy": "undefined", "__eazy_capture_emit": "undefined"}
    assert json.loads((directory / "meta.json").read_text(encoding="utf-8"))["actions"] is True


def test_the_whole_pipeline_runs_on_a_recording(
    recording: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    assert capture_normalize.main([str(recording), "--out", str(work)]) == 0
    assert capture_redact.main([str(work)]) == 0
    assert capture_inventory.main([str(work)]) == 0
    capsys.readouterr()
    redacted = (work / "capture.redacted.json").read_text(encoding="utf-8")
    assert PASSWORD not in redacted
    assert TOKEN not in redacted
    inventory = json.loads((work / "inventory.json").read_text(encoding="utf-8"))
    names = [scenario["name"] for scenario in inventory["scenarios"]]
    assert names[-2:] == ["login", "more threads"]
    login = inventory["scenarios"][-2]
    assert any(action.startswith("click Sign in") for action in login["actions"])
    auth = next(op for op in inventory["operations"] if op["path"] == "/auth")
    assert auth["value_origins"]["body:password"] == {"from": "typed by the user"}
    assert auth["caused_by"]
    threads = next(op for op in inventory["operations"] if op["path"] == "/api/threads")
    assert threads["value_origins"]["query:token"]["from"] == "response body"
    assert {item["name"]: item["behaviour"] for item in threads["query"]}["offset"] == "varies"


def test_traffic_only_mode_injects_nothing(site: str, tmp_path: Path) -> None:
    async def visitor(tab: Any, mark: Callable[[str], Awaitable[None]]) -> None:
        await tab.find(id="login", timeout=10)
        await (await tab.find(id="enter")).click()
        await tab.find(id="more", timeout=10)
        await asyncio.sleep(0.5)

    options = capture_record.Options(
        url=f"{site}/login",
        out=tmp_path / "captures",
        chrome=CHROME,
        headless=True,
        actions=False,
    )
    directory = asyncio.run(capture_record.record(options, visitor))
    kinds = {event["kind"] for event in _timeline(directory)}
    assert "action" not in kinds
    assert "request" in kinds
    capture = capture_normalize.merge([capture_normalize.read_any(directory)])
    assert [e.path for e in capture.exchanges if e.path == "/auth"] == ["/auth"]
