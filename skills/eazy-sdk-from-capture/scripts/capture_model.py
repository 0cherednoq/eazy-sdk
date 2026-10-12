"""The one capture format every script of the skill reads and writes.

A capture is a numbered list of HTTP exchanges plus a timeline of browser events. Bodies are
bytes; in JSON they travel as base64, so a capture survives any site encoding.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

FORMAT = "eazy-capture/1"

type Headers = list[tuple[str, str]]


@dataclass(slots=True)
class Exchange:
    """One request and the response it got."""

    seq: int
    method: str
    url: str
    request_headers: Headers = field(default_factory=list)
    request_body: bytes = b""
    status: int = 0
    response_headers: Headers = field(default_factory=list)
    response_body: bytes = b""
    time: str | None = None
    # Free-form facts the source knew: resource type, initiator, the action that caused it.
    meta: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc

    @property
    def path(self) -> str:
        return urlsplit(self.url).path or "/"

    @property
    def query(self) -> list[tuple[str, str]]:
        return parse_qsl(urlsplit(self.url).query, keep_blank_values=True)

    def request_header(self, name: str) -> str | None:
        return header(self.request_headers, name)

    def response_header(self, name: str) -> str | None:
        return header(self.response_headers, name)


@dataclass(slots=True)
class Capture:
    """Exchanges in the order they happened, and what happened around them."""

    source: str
    files: list[str] = field(default_factory=list)
    exchanges: list[Exchange] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    redacted: bool = False

    def renumber(self) -> None:
        """Give exchanges their final numbers and carry them into the timeline."""
        renamed = {exchange.seq: index for index, exchange in enumerate(self.exchanges, 1)}
        for exchange in self.exchanges:
            exchange.seq = renamed[exchange.seq]
        for event in self.timeline:
            if event.get("exchange") in renamed:
                event["exchange"] = renamed[event["exchange"]]


def header(headers: Headers, name: str) -> str | None:
    """Return the first value of a header, compared case-insensitively."""
    wanted = name.lower()
    for key, value in headers:
        if key.lower() == wanted:
            return value
    return None


def headers_named(headers: Headers, name: str) -> list[str]:
    """Return every value of a repeatable header such as ``Set-Cookie``."""
    wanted = name.lower()
    return [value for key, value in headers if key.lower() == wanted]


def media_type(headers: Headers) -> str:
    """Return the media type without parameters, lower-cased; empty when undeclared."""
    return (header(headers, "content-type") or "").split(";", 1)[0].strip().lower()


def charset(headers: Headers, default: str = "utf-8") -> str:
    """Return the declared charset of a message."""
    for part in (header(headers, "content-type") or "").split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip("\"'").lower()
    return default


def text_of(headers: Headers, body: bytes) -> str:
    """Decode a body for reading; undecodable bytes become replacement characters."""
    try:
        return body.decode(charset(headers), "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def is_textual(headers: Headers) -> bool:
    """Tell whether a body is text a script may search."""
    kind = media_type(headers)
    return (
        kind.startswith("text/")
        or kind.endswith(("json", "xml", "javascript", "x-www-form-urlencoded"))
        or "+json" in kind
        or "+xml" in kind
    )


def _encode(body: bytes) -> str:
    return base64.b64encode(body).decode("ascii")


def to_document(capture: Capture) -> dict[str, Any]:
    """Turn a capture into a JSON-ready document."""
    return {
        "format": FORMAT,
        "source": capture.source,
        "files": capture.files,
        "redacted": capture.redacted,
        "warnings": capture.warnings,
        "exchanges": [
            {
                "seq": item.seq,
                "time": item.time,
                "method": item.method,
                "url": item.url,
                "request": {
                    "headers": [list(pair) for pair in item.request_headers],
                    "body": _encode(item.request_body),
                },
                "response": {
                    "status": item.status,
                    "headers": [list(pair) for pair in item.response_headers],
                    "body": _encode(item.response_body),
                },
                "meta": item.meta,
                "notes": item.notes,
            }
            for item in capture.exchanges
        ],
        "timeline": capture.timeline,
    }


def from_document(document: dict[str, Any]) -> Capture:
    """Read a capture back from its JSON document."""
    if document.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} document: format={document.get('format')!r}")
    capture = Capture(
        source=str(document.get("source", "")),
        files=list(document.get("files", [])),
        timeline=list(document.get("timeline", [])),
        warnings=list(document.get("warnings", [])),
        redacted=bool(document.get("redacted", False)),
    )
    for raw in document["exchanges"]:
        request, response = raw["request"], raw["response"]
        capture.exchanges.append(
            Exchange(
                seq=int(raw["seq"]),
                method=str(raw["method"]),
                url=str(raw["url"]),
                request_headers=[(str(k), str(v)) for k, v in request["headers"]],
                request_body=base64.b64decode(request["body"]),
                status=int(response["status"]),
                response_headers=[(str(k), str(v)) for k, v in response["headers"]],
                response_body=base64.b64decode(response["body"]),
                time=raw.get("time"),
                meta=dict(raw.get("meta", {})),
                notes=list(raw.get("notes", [])),
            )
        )
    return capture


def load(path: Path) -> Capture:
    """Load a capture document from disk."""
    return from_document(json.loads(path.read_text(encoding="utf-8")))


def dump(capture: Capture, path: Path) -> None:
    """Write a capture document to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(to_document(capture), ensure_ascii=False, indent=1)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
