"""Shared fixtures of the skill tests: a small synthetic site recorded three ways.

The site has a login page with a form token, a credentials POST answered by a redirect, a
JSON API behind a ``{status, body}`` envelope and one static image. Nothing in it comes from
a real capture.
"""

from __future__ import annotations

import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "eazy-sdk-from-capture" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CSRF = "c5f3a9d07b1e4c2f8a6d0e9b3f7c1a55"
SESSION = "s9x8w7v6u5t4s3r2q1p0o9n8m7l6k5j4"
API_TOKEN = "tok4f8a2c6e0b9d7f5a3c1e8b6d4f2a0c9"
PASSWORD = "Tr0ub4dor&3x"
EMAIL = "alice.reader@mailbox.example"

LOGIN_PAGE = (
    '<html lang="ru"><head><title>Вход</title></head><body>'
    '<form action="/auth" method="post"><input name="login" type="text">'
    '<input name="password" type="password"><input name="csrf" type="hidden"></form>'
    f'<script>window.state={{"csrf":"{CSRF}","build":"2026.10"}};</script></body></html>'
)
INBOX_PAGE = (
    "<html><head><title>Почта</title></head><body>"
    f'<script>window.boot={{"token":"{API_TOKEN}","email":"{EMAIL}"}};</script></body></html>'
)


def _threads(offset: int) -> bytes:
    items = [
        {"id": str(1000 + offset + index), "subject": f"Тема {index}", "unread": index % 2 == 0}
        for index in range(2)
    ]
    items[0]["flag"] = "pinned"
    body = {"status": 200, "body": {"threads": items, "threads_total": 5}}
    return json.dumps(body, ensure_ascii=False).encode()


@dataclass(frozen=True, slots=True)
class Recorded:
    """One exchange of the synthetic site."""

    method: str
    url: str
    request_headers: tuple[tuple[str, str], ...]
    request_body: bytes
    status: int
    response_headers: tuple[tuple[str, str], ...]
    response_body: bytes


def _api(offset: int) -> Recorded:
    return Recorded(
        "GET",
        f"https://mail.site.example/api/v1/threads?offset={offset}&limit=2&_=1791797500554"
        f"&token={API_TOKEN}",
        (("Host", "mail.site.example"), ("Cookie", f"sid={SESSION}")),
        b"",
        200,
        (("Content-Type", "application/json; charset=utf-8"),),
        _threads(offset),
    )


SITE: tuple[Recorded, ...] = (
    Recorded(
        "GET",
        "https://id.site.example/login?from=mail",
        (("Host", "id.site.example"), ("User-Agent", "probe/1")),
        b"",
        200,
        (
            ("Content-Type", "text/html; charset=UTF-8"),
            ("Set-Cookie", f"act={CSRF}; Domain=.site.example; Path=/; HttpOnly"),
        ),
        LOGIN_PAGE.encode(),
    ),
    Recorded(
        "POST",
        "https://id.site.example/auth?project=mail",
        (
            ("Host", "id.site.example"),
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Origin", "https://id.site.example"),
            ("Cookie", f"act={CSRF}"),
        ),
        f"login=alice.reader%40mailbox.example&password=wrong-pass&csrf={CSRF}&keep=1".encode(),
        302,
        (("Location", "https://id.site.example/login?fail=1"), ("Content-Type", "text/html")),
        b"",
    ),
    Recorded(
        "POST",
        "https://id.site.example/auth?project=mail",
        (
            ("Host", "id.site.example"),
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Origin", "https://id.site.example"),
            ("Cookie", f"act={CSRF}"),
        ),
        (
            f"login=alice.reader%40mailbox.example&password=Tr0ub4dor%263x&csrf={CSRF}&keep=1"
        ).encode(),
        302,
        (
            ("Location", "https://mail.site.example/inbox/"),
            ("Set-Cookie", f"sid={SESSION}; Domain=.site.example; Path=/; Secure; HttpOnly"),
            ("Content-Type", "text/html"),
        ),
        b"",
    ),
    Recorded(
        "GET",
        "https://mail.site.example/inbox/",
        (("Host", "mail.site.example"), ("Cookie", f"act={CSRF}; sid={SESSION}")),
        b"",
        200,
        (("Content-Type", "text/html; charset=UTF-8"),),
        INBOX_PAGE.encode(),
    ),
    _api(0),
    _api(2),
    Recorded(
        "GET",
        "https://static.site.example/img/logo.png",
        (("Host", "static.site.example"),),
        b"",
        200,
        (("Content-Type", "image/png"),),
        b"\x89PNG\r\n\x1a\n\x00\x01",
    ),
)


def _raw(start: str, headers: tuple[tuple[str, str], ...], body: bytes) -> bytes:
    head = "\r\n".join([start, *(f"{name}: {value}" for name, value in headers)])
    return head.encode() + b"\r\n\r\n" + body


def _burp_item(item: Recorded, *, encoded: bool) -> str:
    host = item.url.split("/")[2]
    path = "/" + item.url.split("/", 3)[3]
    request = _raw(f"{item.method} {path} HTTP/1.1", item.request_headers, item.request_body)
    response = _raw(f"HTTP/1.1 {item.status} X", item.response_headers, item.response_body)

    def blob(tag: str, data: bytes) -> str:
        if encoded:
            return f'<{tag} base64="true"><![CDATA[{base64.b64encode(data).decode()}]]></{tag}>'
        # What Burp writes without base64: every byte above 127 becomes a dot.
        lossy = bytes(byte if byte < 128 else 0x2E for byte in data).decode("ascii")
        return f'<{tag} base64="false">{escape(lossy)}</{tag}>'

    return (
        "<item><time>Mon Oct 12 12:31:17 MSK 2026</time>"
        f"<url><![CDATA[{item.url}]]></url><host>{host}</host><port>443</port>"
        f"<protocol>https</protocol><method>{item.method}</method>"
        f"<path><![CDATA[{path}]]></path>{blob('request', request)}"
        f"<status>{item.status}</status>{blob('response', response)}</item>"
    )


def burp_xml(*, encoded: bool) -> bytes:
    """Render the synthetic site as a Burp export, with or without base64."""
    items = "".join(_burp_item(item, encoded=encoded) for item in SITE)
    return f'<?xml version="1.1"?>\n<items burpVersion="2026.9">{items}</items>'.encode()


def har_document() -> dict[str, object]:
    """Render the synthetic site as a HAR 1.2 document."""
    entries = []
    for index, item in enumerate(SITE):
        binary = item.response_body.startswith(b"\x89PNG")
        content: dict[str, object] = {"size": len(item.response_body)}
        if binary:
            content |= {"text": base64.b64encode(item.response_body).decode(), "encoding": "base64"}
        else:
            content["text"] = item.response_body.decode()
        entries.append(
            {
                "startedDateTime": f"2026-10-12T09:31:{17 + index:02d}.000Z",
                "request": {
                    "method": item.method,
                    "url": item.url,
                    "headers": [{"name": k, "value": v} for k, v in item.request_headers],
                    "postData": {"text": item.request_body.decode()} if item.request_body else None,
                },
                "response": {
                    "status": item.status,
                    "headers": [{"name": k, "value": v} for k, v in item.response_headers],
                    "content": content,
                },
            }
        )
    return {"log": {"version": "1.2", "entries": entries}}


@pytest.fixture
def burp_plain(tmp_path: Path) -> Path:
    path = tmp_path / "plain-export"
    path.write_bytes(burp_xml(encoded=False))
    return path


@pytest.fixture
def burp_base64(tmp_path: Path) -> Path:
    path = tmp_path / "capture.xml"
    path.write_bytes(burp_xml(encoded=True))
    return path


@pytest.fixture
def har_file(tmp_path: Path) -> Path:
    path = tmp_path / "traffic.har"
    path.write_text(json.dumps(har_document(), ensure_ascii=False), encoding="utf-8")
    return path
