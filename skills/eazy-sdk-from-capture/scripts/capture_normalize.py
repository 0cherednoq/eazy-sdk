"""Bring a traffic recording of any supported kind to the one capture format.

    python -I capture_normalize.py INPUT [INPUT ...] --out WORKDIR

``INPUT`` is a directory written by ``capture_record.py``, a HAR 1.2 file or a Burp Suite XML
export. The kind is read from the content, never from the file name. The result is
``WORKDIR/capture.json``; everything the source lost on the way is named in its warnings.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import sys
import zlib
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_model import (
    Capture,
    Exchange,
    Headers,
    charset,
    dump,
    header,
    is_textual,
)

_DOTS_IN_MARKUP = re.compile(rb"[>\"']\s*\.{4,}[\s.]*[<\"']")
_XML_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BURP_TIME = re.compile(r"^\w{3} (\w{3}) (\d{1,2}) (\d{2}:\d{2}:\d{2}) \S+ (\d{4})$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class UnknownCaptureError(ValueError):
    """The input is none of the supported recording kinds."""


# --- wire helpers -------------------------------------------------------------------------


def split_message(raw: bytes) -> tuple[str, Headers, bytes]:
    r"""Split a raw HTTP message into its start line, headers and body.

    The blank line may be CRLF or bare LF: an XML parser folds CRLF into LF, so a Burp export
    without base64 never contains ``\r\n\r\n``.
    """
    match = re.search(rb"\r?\n\r?\n", raw)
    head, body = (raw[: match.start()], raw[match.end() :]) if match else (raw, b"")
    lines = head.decode("latin-1").splitlines()
    headers: Headers = []
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers.append((name.strip(), value.strip()))
    return (lines[0] if lines else ""), headers, body


def _dechunk(body: bytes) -> bytes | None:
    out, position = bytearray(), 0
    while True:
        end = body.find(b"\r\n", position)
        if end < 0:
            return None
        try:
            size = int(body[position:end].split(b";", 1)[0].strip(), 16)
        except ValueError:
            return None
        position = end + 2
        if size == 0:
            return bytes(out)
        out += body[position : position + size]
        position += size + 2


def _inflate(body: bytes) -> bytes:
    try:
        return zlib.decompress(body)
    except zlib.error:
        return zlib.decompress(body, -zlib.MAX_WBITS)


_DECODERS: dict[str, Callable[[bytes], bytes]] = {
    "gzip": gzip.decompress,
    "x-gzip": gzip.decompress,
    "deflate": _inflate,
}


def decode_body(headers: Headers, body: bytes, notes: list[str]) -> tuple[Headers, bytes]:
    """Remove transfer and content codings, remembering what was there.

    A coding the standard library cannot undo (``br``, ``zstd``) is left in place and named,
    so the body is never silently treated as text.
    """
    if not body:
        return headers, body
    kept = list(headers)
    if "chunked" in (header(headers, "transfer-encoding") or "").lower():
        plain = _dechunk(body)
        if plain is not None:
            body = plain
            kept = [pair for pair in kept if pair[0].lower() != "transfer-encoding"]
    codings = [
        part.strip().lower()
        for part in (header(headers, "content-encoding") or "").split(",")
        if part.strip() and part.strip().lower() != "identity"
    ]
    for coding in reversed(codings):
        decoder = _DECODERS.get(coding)
        if decoder is None:
            notes.append(f"content-encoding {coding!r} is not decoded: the body is still packed")
            return kept, body
        try:
            body = decoder(body)
        except (OSError, EOFError, zlib.error):
            # A proxy that unpacks bodies usually keeps the header: the body is already plain.
            notes.append(f"content-encoding {coding!r} declared, body was already unpacked")
            break
    if codings:
        notes.append(f"content-encoding removed: {', '.join(codings)}")
        kept = [pair for pair in kept if pair[0].lower() != "content-encoding"]
    return kept, body


def looks_lossy(headers: Headers, body: bytes) -> bool:
    """Tell whether a text body lost its non-ASCII bytes on export.

    A lossy exporter writes a dot for every byte it could not keep: a body declared as text
    then has no byte above 127 and runs of dots where markup text should be.
    """
    if not body or not is_textual(headers):
        return False
    if any(byte > 127 for byte in body):
        return False
    return _DOTS_IN_MARKUP.search(body) is not None


# --- Burp XML -----------------------------------------------------------------------------


def _burp_blob(element: ElementTree.Element | None) -> tuple[bytes, bool]:
    if element is None or element.text is None:
        return b"", True
    if element.get("base64") == "true":
        return base64.b64decode(element.text), True
    return element.text.encode("utf-8"), False


def _burp_time(text: str) -> str | None:
    match = _BURP_TIME.match(text.strip())
    if match is None or match.group(1) not in _MONTHS:
        return None
    month = _MONTHS.index(match.group(1)) + 1
    return f"{match.group(4)}-{month:02d}-{int(match.group(2)):02d}T{match.group(3)}"


def read_burp(raw: bytes, name: str) -> Capture:
    """Read a Burp Suite "Save items" XML export."""
    # Burp declares XML 1.1, which expat rejects, and may keep NUL bytes of binary bodies.
    # A binary body exported without base64 also leaves control bytes no XML 1.0 parser takes.
    patched = raw.replace(b'<?xml version="1.1"', b'<?xml version="1.0"', 1)
    patched, controls = _XML_CONTROL.subn(b".", patched)
    root = ElementTree.fromstring(patched)  # noqa: S314 - a local file the user recorded
    capture = Capture(source="burp-xml", files=[name])
    if controls:
        capture.warnings.append(
            f"{name}: {controls} control bytes of binary bodies were replaced to read the XML"
        )
    plain = 0
    for seq, item in enumerate(root.findall("item"), 1):
        request_raw, request_exact = _burp_blob(item.find("request"))
        response_raw, response_exact = _burp_blob(item.find("response"))
        exact = request_exact and response_exact
        plain += not exact
        notes: list[str] = []
        _, request_headers, request_body = split_message(request_raw)
        start, response_headers, response_body = split_message(response_raw)
        request_headers, request_body = decode_body(request_headers, request_body, notes)
        response_headers, response_body = decode_body(response_headers, response_body, notes)
        status_text = (item.findtext("status") or "").strip()
        status = int(status_text) if status_text.isdigit() else _status_of(start)
        exchange = Exchange(
            seq=seq,
            method=(item.findtext("method") or "GET").strip(),
            url=(item.findtext("url") or "").strip(),
            request_headers=request_headers,
            request_body=request_body,
            status=status,
            response_headers=response_headers,
            response_body=response_body,
            time=_burp_time(item.findtext("time") or ""),
            notes=notes,
        )
        if not exact and looks_lossy(response_headers, response_body):
            exchange.meta["lossy"] = True
            notes.append("non-ASCII bytes of the response were replaced with dots on export")
        capture.exchanges.append(exchange)
    if plain:
        capture.warnings.append(
            f"{name}: {plain} of {len(capture.exchanges)} items were exported without base64. "
            "Such an export keeps no byte above 127 and no binary body: good for the inventory, "
            "not for replay. Re-export from Burp with 'Base64-encode requests and responses'."
        )
    return capture


def _status_of(start_line: str) -> int:
    parts = start_line.split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0


# --- HAR ----------------------------------------------------------------------------------


def _har_headers(items: Any) -> Headers:
    return [(str(item["name"]), str(item["value"])) for item in items or []]


def _har_request_body(request: dict[str, Any], headers: Headers) -> bytes:
    post = request.get("postData") or {}
    text = post.get("text")
    if isinstance(text, str):
        if post.get("encoding") == "base64":
            return base64.b64decode(text)
        return text.encode(charset(headers), "replace")
    return b""


def _har_response_body(content: dict[str, Any], headers: Headers, notes: list[str]) -> bytes:
    text = content.get("text")
    if not isinstance(text, str):
        if int(content.get("size") or 0) > 0:
            notes.append("the response body was not saved by the recorder")
        return b""
    if content.get("encoding") == "base64":
        return base64.b64decode(text)
    try:
        return text.encode(charset(headers), "replace")
    except LookupError:
        return text.encode("utf-8")


def read_har(document: dict[str, Any], name: str) -> Capture:
    """Read a HAR 1.2 document."""
    capture = Capture(source="har", files=[name])
    for seq, entry in enumerate(document["log"].get("entries", []), 1):
        request, response = entry["request"], entry.get("response") or {}
        notes: list[str] = []
        request_headers = _har_headers(request.get("headers"))
        response_headers = _har_headers(response.get("headers"))
        body = _har_response_body(response.get("content") or {}, response_headers, notes)
        # HAR text is already decoded: the codings describe the wire, not what was saved.
        response_headers = [
            pair
            for pair in response_headers
            if pair[0].lower() not in {"content-encoding", "transfer-encoding"}
        ]
        exchange = Exchange(
            seq=seq,
            method=str(request.get("method", "GET")),
            url=str(request.get("url", "")),
            request_headers=request_headers,
            request_body=_har_request_body(request, request_headers),
            status=int(response.get("status") or 0),
            response_headers=response_headers,
            response_body=body,
            time=entry.get("startedDateTime"),
            notes=notes,
        )
        for key in ("_resourceType", "_initiator", "_webSocketMessages"):
            if key in entry:
                exchange.meta[key.lstrip("_")] = entry[key]
        capture.exchanges.append(exchange)
    return capture


# --- own recording ------------------------------------------------------------------------


def _recorded_body(directory: Path, part: dict[str, Any]) -> bytes:
    name = part.get("body_file")
    return (directory / str(name)).read_bytes() if name else b""


def read_recording(directory: Path) -> Capture:
    """Read a directory written by ``capture_record.py``."""
    capture = Capture(source="recording", files=[directory.name])
    for path in sorted((directory / "exchanges").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        request, response = raw["request"], raw.get("response") or {}
        notes = list(raw.get("notes", []))
        if raw.get("failed"):
            notes.append(f"the request failed: {raw['failed']}")
        if response.get("body_omitted"):
            notes.append(f"the response body was not kept: {response['body_omitted']}")
        capture.exchanges.append(
            Exchange(
                seq=int(raw["seq"]),
                method=str(request["method"]),
                url=str(request["url"]),
                request_headers=[(str(k), str(v)) for k, v in request.get("headers", [])],
                request_body=_recorded_body(directory, request),
                status=int(response.get("status") or 0),
                response_headers=[(str(k), str(v)) for k, v in response.get("headers", [])],
                response_body=_recorded_body(directory, response),
                time=raw.get("time"),
                meta=dict(raw.get("meta", {})),
                notes=notes,
            )
        )
    timeline = directory / "timeline.jsonl"
    if timeline.exists():
        for line in timeline.read_text(encoding="utf-8").splitlines():
            if line.strip():
                capture.timeline.append(json.loads(line))
    # Actions reach the recorder a poll later than requests do: order by the common clock.
    capture.timeline.sort(key=lambda event: (float(event.get("t", 0)), int(event.get("seq", 0))))
    for event in capture.timeline:
        if event.get("kind") == "storage":
            _attach_storage(directory, event)
    capture.exchanges.sort(key=lambda item: item.seq)
    return capture


def _attach_storage(directory: Path, event: dict[str, Any]) -> None:
    """Carry the values of web storage into the timeline: a token may come from there."""
    slug = re.sub(r"[^A-Za-z0-9.-]+", "_", str(event.get("origin", ""))).strip("_")[:80] or "site"
    path = directory / "storage" / f"{slug}.json"
    if path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
        event["local"] = stored.get("local", {})
        event["session"] = stored.get("session", {})


def typed_values(directory: Path) -> list[dict[str, Any]]:
    """Read what the person typed during a recording."""
    path = directory / "secrets.local.json"
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("typed", []))


# --- entry --------------------------------------------------------------------------------


def read_any(path: Path) -> Capture:
    """Read one input, recognising its kind by content."""
    if path.is_dir():
        if (path / "exchanges").is_dir():
            return read_recording(path)
        raise UnknownCaptureError(f"{path}: a directory without exchanges/ is not a recording")
    raw = path.read_bytes()
    head = raw[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<"):
        if b"<items" not in raw[:65536]:
            raise UnknownCaptureError(f"{path}: XML, but not a Burp items export")
        return read_burp(raw, path.name)
    if head.startswith(b"{"):
        document = json.loads(raw.decode("utf-8-sig"))
        if isinstance(document, dict) and isinstance(document.get("log"), dict):
            return read_har(document, path.name)
        raise UnknownCaptureError(f"{path}: JSON, but not a HAR document")
    raise UnknownCaptureError(f"{path}: neither a recording directory, HAR nor Burp XML")


def merge(captures: Sequence[Capture]) -> Capture:
    """Join several inputs into one timeline, ordered by time when every exchange has one."""
    if len(captures) == 1:
        merged = captures[0]
    else:
        merged = Capture(source="+".join(sorted({item.source for item in captures})))
        offset = 0
        for item in captures:
            merged.files += item.files
            merged.warnings += item.warnings
            for exchange in item.exchanges:
                exchange.seq += offset
            for event in item.timeline:
                if isinstance(event.get("exchange"), int):
                    event["exchange"] += offset
            merged.exchanges += item.exchanges
            merged.timeline += item.timeline
            offset += len(item.exchanges)
        if all(exchange.time for exchange in merged.exchanges):
            merged.exchanges.sort(key=lambda exchange: (exchange.time or "", exchange.seq))
    merged.renumber()
    if not merged.timeline:
        merged.timeline = [
            {"kind": "request", "exchange": exchange.seq, "time": exchange.time}
            for exchange in merged.exchanges
        ]
    lossy = [exchange.seq for exchange in merged.exchanges if exchange.meta.get("lossy")]
    if lossy:
        merged.warnings.append(
            f"exchanges {lossy} lost their non-ASCII text: a response case that matches on "
            "such text cannot be proven by replay"
        )
    unsaved = [
        exchange.seq
        for exchange in merged.exchanges
        if any("not saved" in note or "not kept" in note for note in exchange.notes)
    ]
    if unsaved:
        merged.warnings.append(f"exchanges {unsaved} have no saved response body")
    return merged


def summary(capture: Capture) -> str:
    """Describe a capture in a few lines for the console."""
    hosts = Counter(exchange.host for exchange in capture.exchanges)
    lines = [
        f"source: {capture.source} ({', '.join(capture.files)})",
        f"exchanges: {len(capture.exchanges)}, timeline events: {len(capture.timeline)}",
        "hosts: " + ", ".join(f"{host} x{count}" for host, count in hosts.most_common()),
    ]
    lines += [f"WARNING: {warning}" for warning in capture.warnings]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Normalise the inputs and write ``capture.json``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="work directory")
    args = parser.parse_args(argv)
    capture = merge([read_any(path) for path in args.inputs])
    capture.files = [str(path) for path in args.inputs]
    target = args.out / "capture.json"
    dump(capture, target)
    typed = [item for path in args.inputs if path.is_dir() for item in typed_values(path)]
    if typed:
        (args.out / "typed.json").write_text(
            json.dumps({"typed": typed}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"{summary(capture)}\nwritten: {target} at {stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
