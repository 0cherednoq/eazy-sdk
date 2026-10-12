"""Collect the facts an SDK is written from.

    python -I capture_inventory.py WORKDIR

Reads ``WORKDIR/capture.redacted.json`` and writes ``inventory.json`` and ``inventory.md``.
The inventory states only what the capture shows: operations, their parameters and where each
value came from, response variants, redirect chains, cookies, envelopes, pagination hints,
noise and signals of things this skill does not solve. What was not observed is absent.

Response schemas carry types, never values: the model learns the shape of a mailbox without
reading the mail.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_model import (
    Capture,
    Exchange,
    headers_named,
    is_textual,
    load,
    media_type,
    text_of,
)

INVENTORY_FORMAT = "eazy-inventory/1"

_PLACEHOLDER = re.compile(r"__SECRET_([a-z0-9]+)_\d+__")
_STATIC_SUFFIXES = (
    ".js", ".mjs", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".webm", ".mp3", ".avif",
)  # fmt: skip
_STATIC_TYPES = {"image", "font", "stylesheet", "media", "script", "manifest", "texttrack"}
_TELEMETRY_HOST = re.compile(
    r"google-analytics|googletagmanager|doubleclick|googlesyndication|mc\.yandex|metrika|"
    r"facebook\.(com|net)|sentry|hotjar|segment\.(io|com)|amplitude|mixpanel|newrelic|"
    r"tns-counter|top-fwz1|clarity\.ms|cspreport|csp\.",
    re.IGNORECASE,
)
_TELEMETRY_PATH = re.compile(r"csp[-_]?report|/beacon|/collect\b|/telemetry|/rum\b|/pixel")
_TRANSPORT_HEADERS = {
    "host", "connection", "content-length", "accept-encoding", "user-agent", "accept",
    "accept-language", "cache-control", "pragma", "upgrade-insecure-requests", "cookie", "te",
    "priority", "dnt", "if-none-match", "if-modified-since", "range",
}  # fmt: skip
_PAGING_REQUEST = {
    "offset", "limit", "page", "per_page", "perpage", "page_size", "pagesize", "size", "count",
    "cursor", "after", "before", "next", "start", "skip", "take", "from", "pageno", "p",
}  # fmt: skip
_PAGING_CURSOR = {"cursor", "after", "before", "next", "next_token", "page_token", "offset_id"}
_PAGING_RESPONSE = re.compile(
    r"total|count$|has_?more|next|cursor|pages?$|page_?count|offset|last_?id", re.IGNORECASE
)
_SIGNATURE_NAME = re.compile(r"sign(ature)?$|^sig$|hmac|hash$|checksum|nonce", re.IGNORECASE)
_CHALLENGE = re.compile(
    rb"cf-chl|__cf_chl|challenge-platform|datadome|px-captcha|perimeterx|g-recaptcha|hcaptcha|"
    rb"smartcaptcha|incapsula|_Incapsula_|akamai-bm|bm-verify",
    re.IGNORECASE,
)
_META_REFRESH = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["']\s*\d+\s*;\s*url=['"]?([^'">]+)""",
    re.IGNORECASE,
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.IGNORECASE | re.DOTALL)
_INPUT = re.compile(r"<(?:input|select|textarea)\b([^>]*)>", re.IGNORECASE)
_ATTRIBUTE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
# A home-made generator may drop leading zeros of a group: still a random request id.
_LOOSE_UUID = re.compile(r"^[0-9a-f]{1,8}(-[0-9a-f]{1,4}){3}-[0-9a-f]{1,12}$", re.IGNORECASE)
_HEX = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)
_TOKENISH = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[\w.~=-]{20,}$")
_QUERY_STRING = re.compile(r"^[\w.%-]+=[^&=]*(&[\w.%-]+=[^&=]*)+$")
_ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_MIN_TRACED = 6
_MAX_LABEL = 32
_MAX_MESSAGE_LINES = 5
_CONTROL_TAGS = {"button", "input", "select", "textarea", "label", "summary"}
_IDENTITY_KINDS = {"email", "emaillocal", "emaildomain", "login", "password", "typed"}
_MERGE_THRESHOLD = 3
_MAX_VALUES = 5
_MAX_DEPTH = 7
_MAX_PROPERTIES = 80
_REDIRECT_LOW, _REDIRECT_HIGH = 300, 399
_CONTEXT_BEFORE, _CONTEXT_AFTER = 48, 12


# --- noise --------------------------------------------------------------------------------


def noise_reason(exchange: Exchange) -> str | None:
    """Say why an exchange is not part of the API, or ``None`` when it is."""
    path = exchange.path.lower()
    resource = str(exchange.meta.get("resourceType", "")).lower()
    if exchange.method == "OPTIONS":
        return "CORS preflight"
    if _TELEMETRY_HOST.search(exchange.host) or _TELEMETRY_PATH.search(path):
        return "analytics or reporting"
    if resource in _STATIC_TYPES or path.endswith(_STATIC_SUFFIXES):
        return "static resource"
    kind = media_type(exchange.response_headers)
    if kind.startswith(("image/", "font/", "video/", "audio/")):
        return "static resource"
    if exchange.status == 0:
        return "no response was recorded"
    return None


# --- path templates -----------------------------------------------------------------------


def _segment_kind(segment: str) -> str | None:
    if segment.isdigit():
        return "{id}"
    if _UUID.match(segment):
        return "{uuid}"
    if _PLACEHOLDER.fullmatch(segment):
        return "{secret}"
    if _HEX.match(segment):
        return "{hex}"
    if _TOKENISH.match(segment):
        return "{token}"
    return None


def path_template(path: str) -> str:
    """Replace the segments of a path that look like identifiers."""
    parts = [_segment_kind(part) or part for part in path.split("/")]
    return "/".join(parts) or "/"


def merge_templates(keys: Iterable[tuple[str, str, str]]) -> dict[tuple[str, str, str], str]:
    """Fold paths that differ in one segment across several samples into one template."""
    result = {key: key[2] for key in keys}
    families: dict[tuple[str, str, int], list[tuple[str, str, str]]] = defaultdict(list)
    for key in result:
        families[key[0], key[1], key[2].count("/")].append(key)
    for members in families.values():
        split = [key[2].split("/") for key in members]
        for position in range(len(split[0])):
            groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
            for index, parts in enumerate(split):
                groups[tuple(parts[:position] + parts[position + 1 :])].append(index)
            for indexes in groups.values():
                values = {split[index][position] for index in indexes}
                if len(values) < _MERGE_THRESHOLD or any(v.startswith("{") for v in values):
                    continue
                # `/login`, `/scroll`, `/logout` are three pages, not one page with a
                # parameter: only values that look issued (they carry digits) are folded.
                if sum(any(c.isdigit() for c in v) for v in values) * 2 < len(values):
                    continue
                for index in indexes:
                    parts = list(split[index])
                    parts[position] = "{param}"
                    result[members[index]] = "/".join(parts)
    return result


# --- values -------------------------------------------------------------------------------


def classify_value(value: str) -> str:
    """Name what a single request value looks like."""
    secret = _PLACEHOLDER.fullmatch(value)
    if secret:
        return f"secret:{secret.group(1)}"
    if value.isdigit() and len(value) in {10, 13} and value[0] in "12":
        return "timestamp"
    if _LOOSE_UUID.match(value):
        return "uuid"
    if value[:1] in "{[":
        try:
            json.loads(value)
        except ValueError:
            pass
        else:
            return "json"
    if value.startswith(("http://", "https://")):
        return "url"
    if _QUERY_STRING.match(value):
        return "query-string"
    if value in {"true", "false"}:
        return "boolean"
    if re.fullmatch(r"-?\d+", value):
        return "integer"
    return "string"


def _shown(value: str, limit: int = 90) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


@dataclass(slots=True)
class Parameter:
    """One named request value across the samples of an operation."""

    name: str
    values: list[str] = field(default_factory=list)
    seen: int = 0

    def add(self, value: str) -> None:
        self.seen += 1
        if value not in self.values:
            self.values.append(value)

    def describe(self, samples: int) -> dict[str, Any]:
        kinds = sorted({classify_value(value) for value in self.values})
        if len(kinds) == 1 and (kinds[0].startswith("secret:") or kinds[0] == "timestamp"):
            behaviour = kinds[0]
        elif len(self.values) == 1:
            behaviour = "constant"
        else:
            behaviour = "varies"
        described: dict[str, Any] = {
            "name": self.name,
            "behaviour": behaviour,
            "looks_like": kinds,
            "samples": f"{self.seen}/{samples}",
            "values": [_shown(value) for value in self.values[:_MAX_VALUES]],
        }
        if len(self.values) > _MAX_VALUES:
            described["more_values"] = len(self.values) - _MAX_VALUES
        numeric = kinds == ["integer"]
        if self.name.lower() in _PAGING_CURSOR or (
            self.name.lower() in _PAGING_REQUEST and numeric
        ):
            described["hint"] = "pagination"
        if _SIGNATURE_NAME.search(self.name):
            described["hint"] = "signature"
        return described


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict) and depth < 3:
        for key, item in value.items():
            yield from _flatten(item, f"{prefix}.{key}" if prefix else str(key), depth + 1)
    elif isinstance(value, (dict, list)):
        yield prefix, json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(value, bool):
        yield prefix, "true" if value else "false"
    elif value is None:
        yield prefix, "null"
    else:
        yield prefix, str(value)


def request_body_fields(exchange: Exchange) -> tuple[str, list[tuple[str, str]]]:
    """Return the body encoding of a request and its named values in wire order."""
    if not exchange.request_body:
        return "none", []
    kind = media_type(exchange.request_headers)
    text = text_of(exchange.request_headers, exchange.request_body)
    if kind == "application/x-www-form-urlencoded":
        return "form", parse_qsl(text, keep_blank_values=True)
    if "json" in kind:
        try:
            return "json", list(_flatten(json.loads(text)))
        except ValueError:
            return "json (unparsed)", []
    if kind.startswith("multipart/"):
        names = re.findall(r'Content-Disposition: form-data; name="([^"]+)"', text)
        return "multipart", [(name, "") for name in names]
    return (kind or "bytes"), []


def path_parameters(template: str, path: str) -> list[tuple[str, str]]:
    """Pair the placeholders of a template with the segments of a concrete path."""
    pairs = []
    for index, (pattern, actual) in enumerate(
        zip(template.split("/"), path.split("/"), strict=False)
    ):
        if pattern.startswith("{"):
            pairs.append((f"path[{index}]", actual))
    return pairs


# --- schema -------------------------------------------------------------------------------


def _string_format(value: str) -> str:
    if _PLACEHOLDER.fullmatch(value):
        return "secret"
    if value.startswith(("http://", "https://", "//")):
        return "url"
    if _ISO_TIME.match(value):
        return "datetime"
    if re.fullmatch(r"-?\d+(\.\d+)?", value):
        return "numeric-string"
    return ""


def infer_schema(value: Any, depth: int = 0) -> dict[str, Any]:
    """Describe the shape of one JSON value without its content."""
    if isinstance(value, bool):
        return {"type": ["boolean"]}
    if isinstance(value, int):
        return {"type": ["integer"]}
    if isinstance(value, float):
        return {"type": ["number"]}
    if value is None:
        return {"type": ["null"]}
    if isinstance(value, str):
        schema: dict[str, Any] = {"type": ["string"]}
        fmt = _string_format(value)
        if fmt:
            schema["format"] = [fmt]
        return schema
    if depth >= _MAX_DEPTH:
        return {"type": ["array" if isinstance(value, list) else "object"], "truncated": True}
    if isinstance(value, list):
        schema = {"type": ["array"], "length": [len(value), len(value)]}
        items: dict[str, Any] | None = None
        for item in value:
            items = merge_schema(items, infer_schema(item, depth + 1))
        if items is not None:
            schema["items"] = items
        return schema
    properties = {
        str(key): infer_schema(item, depth + 1)
        for key, item in list(value.items())[:_MAX_PROPERTIES]
    }
    schema = {"type": ["object"], "count": 1, "properties": properties}
    for name in properties:
        properties[name]["present"] = 1
    if len(value) > _MAX_PROPERTIES:
        schema["truncated"] = True
    return schema


def merge_schema(left: dict[str, Any] | None, right: dict[str, Any]) -> dict[str, Any]:
    """Union two shapes: a field seen in only some objects ends up optional."""
    if left is None:
        return right
    merged: dict[str, Any] = {"type": sorted(set(left["type"]) | set(right["type"]))}
    formats = sorted(set(left.get("format", [])) | set(right.get("format", [])))
    if formats and "string" in merged["type"]:
        merged["format"] = formats
    if "length" in left or "length" in right:
        lengths = [*left.get("length", []), *right.get("length", [])]
        merged["length"] = [min(lengths), max(lengths)]
    if "items" in left or "items" in right:
        merged["items"] = (
            merge_schema(left.get("items"), right["items"]) if "items" in right else left["items"]
        )
    if "properties" in left or "properties" in right:
        merged["count"] = left.get("count", 0) + right.get("count", 0)
        properties: dict[str, Any] = {}
        mine, theirs = left.get("properties", {}), right.get("properties", {})
        for name in [*mine, *[key for key in theirs if key not in mine]]:
            if name in mine and name in theirs:
                present = mine[name].get("present", 0) + theirs[name].get("present", 0)
                properties[name] = merge_schema(mine[name], theirs[name])
            else:
                only = mine.get(name) or theirs[name]
                present = only.get("present", 0)
                properties[name] = dict(only)
            properties[name]["present"] = present
        merged["properties"] = properties
    if left.get("truncated") or right.get("truncated"):
        merged["truncated"] = True
    return merged


def finish_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Turn presence counters into ``optional`` marks and drop the counters."""
    out = {key: value for key, value in schema.items() if key not in {"present", "count"}}
    if "items" in schema:
        out["items"] = finish_schema(schema["items"])
    if "properties" in schema:
        total = schema.get("count", 1)
        out["properties"] = {}
        for name, item in schema["properties"].items():
            finished = finish_schema(item)
            if item.get("present", total) < total:
                finished["optional"] = True
            out["properties"][name] = finished
    return out


# --- responses ----------------------------------------------------------------------------


def _attributes(text: str) -> dict[str, str]:
    return {
        match.group(1).lower(): match.group(2) or match.group(3) or match.group(4) or ""
        for match in _ATTRIBUTE.finditer(text)
    }


def html_facts(exchange: Exchange) -> dict[str, Any]:
    """Describe an HTML page: title, forms and their fields, a client-side redirect."""
    text = text_of(exchange.response_headers, exchange.response_body)
    facts: dict[str, Any] = {}
    title = _TITLE.search(text)
    if title and not exchange.meta.get("lossy"):
        facts["title"] = _shown(" ".join(title.group(1).split()), 80)
    forms = []
    for attributes, inner in _FORM.findall(text)[:6]:
        described = _attributes(attributes)
        fields = []
        for raw in _INPUT.findall(inner)[:30]:
            item = _attributes(raw)
            if item.get("name"):
                fields.append({"name": item["name"], "type": item.get("type", "text")})
        forms.append(
            {
                "action": described.get("action", ""),
                "method": described.get("method", "get").upper(),
                "fields": fields,
            }
        )
    if forms:
        facts["forms"] = forms
    refresh = _META_REFRESH.search(text)
    if refresh:
        facts["meta_refresh"] = urljoin(exchange.url, refresh.group(1).strip())
    if _CHALLENGE.search(exchange.response_body):
        facts["protection_markers"] = True
    return facts


def _location(exchange: Exchange) -> str | None:
    value = exchange.response_header("location")
    return urljoin(exchange.url, value) if value else None


def _location_shape(url: str) -> str:
    parts = urlsplit(url)
    keys = sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)})
    return f"{parts.netloc}{parts.path}" + (f" ?{','.join(keys)}" if keys else "")


def _json_body(exchange: Exchange) -> Any:
    if "json" not in media_type(exchange.response_headers) or not exchange.response_body:
        return None
    try:
        return json.loads(text_of(exchange.response_headers, exchange.response_body))
    except ValueError:
        return None


def _discriminators(body: Any) -> dict[str, Any]:
    """Top-level scalars that can tell one outcome from another: ``status``, ``ok``."""
    if not isinstance(body, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in body.items():
        if (
            isinstance(value, bool)
            or (isinstance(value, int) and abs(value) < 1000)
            or (isinstance(value, str) and re.fullmatch(r"[A-Za-z_]{2,24}", value))
        ):
            out[str(key)] = value
    return out


def _page_kind(exchange: Exchange) -> str:
    """Tell apart HTML pages one address serves: a redirect stub is not the real page."""
    if "html" not in media_type(exchange.response_headers):
        return ""
    facts = html_facts(exchange)
    if "meta_refresh" in facts:
        return "refresh " + _location_shape(facts["meta_refresh"])
    forms = ",".join(form["action"] for form in facts.get("forms", []))
    return f"title {facts.get('title', '')} forms {forms}"


def response_variants(samples: Sequence[Exchange]) -> list[dict[str, Any]]:
    """Group the responses of one operation by what distinguishes an outcome."""
    groups: dict[tuple[Any, ...], list[Exchange]] = defaultdict(list)
    for exchange in samples:
        location = _location(exchange)
        marks = _discriminators(_json_body(exchange))
        key = (
            exchange.status,
            media_type(exchange.response_headers),
            _location_shape(location) if location else "",
            json.dumps(marks, sort_keys=True),
            _page_kind(exchange),
        )
        groups[key].append(exchange)
    variants = []
    for (status, media, location_shape, marks_text, _), members in groups.items():
        marks = json.loads(marks_text)
        variant: dict[str, Any] = {
            "status": status,
            "media_type": media,
            "exchanges": [exchange.seq for exchange in members],
            "body_bytes": [len(exchange.response_body) for exchange in members],
        }
        if location_shape:
            variant["location"] = location_shape
            variant["location_examples"] = [
                _shown(_location(exchange) or "", 200) for exchange in members[:2]
            ]
        if marks:
            variant["body_marks"] = marks
        cookies = sorted(
            {
                value.split("=", 1)[0].strip()
                for exchange in members
                for value in headers_named(exchange.response_headers, "set-cookie")
            }
        )
        if cookies:
            variant["sets_cookies"] = cookies
        schema: dict[str, Any] | None = None
        for exchange in members:
            body = _json_body(exchange)
            if body is not None:
                schema = merge_schema(schema, infer_schema(body))
        if schema is not None:
            variant["json_schema"] = finish_schema(schema)
        elif "html" in media:
            variant["html"] = html_facts(members[0])
        if any(exchange.meta.get("lossy") for exchange in members):
            variant["lossy"] = True
        variants.append(variant)
    return variants


# --- provenance ---------------------------------------------------------------------------


def _json_pointer(body: Any, wanted: str, prefix: str = "", depth: int = 0) -> str | None:
    if depth > _MAX_DEPTH:
        return None
    if isinstance(body, dict):
        for key, item in body.items():
            found = _json_pointer(item, wanted, f"{prefix}/{key}", depth + 1)
            if found:
                return found
    elif isinstance(body, list):
        for index, item in enumerate(body[:50]):
            found = _json_pointer(item, wanted, f"{prefix}/{index}", depth + 1)
            if found:
                return found
    elif isinstance(body, (str, int, float)) and not isinstance(body, bool) and str(body) == wanted:
        return prefix or "/"
    return None


def _regex_hint(before: str) -> str | None:
    """Suggest a pattern from the text right before a value: ``"csrf":"`` → ``"csrf":"([^"]+)"``."""
    match = re.search(r"""(["']?[\w-]{2,40}["']?\s*[:=]\s*)(["'])$""", before)
    if match is None:
        return None
    quote_mark = match.group(2)
    return f"{re.escape(match.group(1))}{quote_mark}([^{quote_mark}]+){quote_mark}".replace(
        "\\ ", " "
    )


class Provenance:
    """Finds where a value of a request first appeared in the capture."""

    def __init__(self, capture: Capture, typed_kinds: frozenset[str]) -> None:
        self._exchanges = capture.exchanges
        self._typed_kinds = typed_kinds
        self._bodies = {
            exchange.seq: text_of(exchange.response_headers, exchange.response_body)
            for exchange in capture.exchanges
            if exchange.response_body and is_textual(exchange.response_headers)
        }
        self._stored: dict[str, dict[str, Any]] = {}
        for event in capture.timeline:
            if event.get("kind") != "storage":
                continue
            for area in ("local", "session"):
                for key, value in (event.get(area) or {}).items():
                    self._stored.setdefault(
                        str(value),
                        {
                            "from": "web storage",
                            "area": area,
                            "key": str(key),
                            "origin": event.get("origin", ""),
                        },
                    )
        self._cache: dict[tuple[str, int], dict[str, Any]] = {}

    def of(self, value: str, before: int) -> dict[str, Any]:
        """Describe the origin of ``value`` as sent by exchange number ``before``."""
        key = (value, before)
        if key not in self._cache:
            self._cache[key] = self._find(value, before)
        return self._cache[key]

    def _find(self, value: str, before: int) -> dict[str, Any]:
        secret = _PLACEHOLDER.fullmatch(value)
        if secret and secret.group(1) in self._typed_kinds:
            return {"from": "typed by the user"}
        spellings = {value, quote(value, safe=""), json.dumps(value)[1:-1]}
        identity = bool(secret) and secret.group(1) in _IDENTITY_KINDS if secret else False
        for exchange in self._exchanges:
            if exchange.seq >= before:
                break
            # The client said it before any response did: the value is the client's own.
            if self._in_request(exchange, spellings):
                if identity:
                    return {"from": "the caller"}
                return {"from": "the client", "first_sent_in": exchange.seq}
            found = self._in_response(exchange, value, spellings)
            if found is not None:
                return found
        if identity:
            return {"from": "the caller"}
        if value in self._stored:
            # No response carried it, the page keeps it in storage: a script made it.
            return self._stored[value]
        return {"from": "unknown", "note": "not found in any earlier response"}

    @staticmethod
    def _in_request(exchange: Exchange, spellings: set[str]) -> bool:
        body = text_of(exchange.request_headers, exchange.request_body)
        return any(item in exchange.url or item in body for item in spellings)

    def _in_response(
        self, exchange: Exchange, value: str, spellings: set[str]
    ) -> dict[str, Any] | None:
        for cookie in headers_named(exchange.response_headers, "set-cookie"):
            name, _, rest = cookie.partition("=")
            if rest.split(";", 1)[0].strip('"') == value:
                return {"from": "set-cookie", "exchange": exchange.seq, "cookie": name.strip()}
        location = exchange.response_header("location")
        if location and any(item in location for item in spellings):
            for name, item in parse_qsl(urlsplit(location).query, keep_blank_values=True):
                if item == value:
                    return {"from": "location", "exchange": exchange.seq, "query": name}
            return {"from": "location", "exchange": exchange.seq}
        for name, item in exchange.response_headers:
            if name.lower() not in {"set-cookie", "location"} and item == value:
                return {"from": "response header", "exchange": exchange.seq, "header": name}
        body = self._bodies.get(exchange.seq)
        if body is None:
            return None
        for spelling in spellings:
            index = body.find(spelling)
            if index < 0:
                continue
            found: dict[str, Any] = {"from": "response body", "exchange": exchange.seq}
            parsed = _json_body(exchange)
            pointer = _json_pointer(parsed, value) if parsed is not None else None
            if pointer:
                found["json_pointer"] = pointer
            else:
                before = body[max(0, index - _CONTEXT_BEFORE) : index]
                after = body[index + len(spelling) : index + len(spelling) + _CONTEXT_AFTER]
                found["context"] = f"{before}⟦value⟧{after}".replace("\n", " ")
                hint = _regex_hint(before)
                if hint:
                    found["regex"] = hint
                found["occurrences"] = body.count(spelling)
            return found
        return None


# --- operations ---------------------------------------------------------------------------


def _traced(value: str) -> bool:
    """Tell whether a value is worth looking for in earlier responses.

    A secret always is. Otherwise only something that looks issued by the server: long
    enough and carrying a digit. A word such as ``web`` occurs in every page by chance.
    """
    if _PLACEHOLDER.fullmatch(value):
        return True
    return (
        len(value) >= _MIN_TRACED + 2
        and classify_value(value) in {"string", "integer"}
        and any(char.isdigit() for char in value)
    )


def _cookie_names(exchange: Exchange) -> list[str]:
    value = exchange.request_header("cookie") or ""
    return [part.split("=", 1)[0].strip() for part in value.split(";") if "=" in part]


def _duplicates(groups: dict[str, dict[str, Parameter]]) -> list[list[str]]:
    by_values: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for place, parameters in groups.items():
        for parameter in parameters.values():
            longest = max((len(value) for value in parameter.values), default=0)
            if longest >= _MIN_TRACED:
                by_values[tuple(parameter.values)].append(f"{place}:{parameter.name}")
    return [names for names in by_values.values() if len(names) > 1]


def describe_operation(
    method: str, host: str, template: str, samples: Sequence[Exchange], provenance: Provenance
) -> dict[str, Any]:
    """Describe one operation from all of its samples."""
    groups: dict[str, dict[str, Parameter]] = {
        place: {} for place in ("path_params", "query", "body", "header")
    }
    encodings: set[str] = set()
    body_order: list[str] = []
    origins: dict[str, dict[str, Any]] = {}
    cookies: dict[str, int] = defaultdict(int)

    def note(place: str, name: str, value: str, exchange: Exchange) -> None:
        groups[place].setdefault(name, Parameter(name)).add(value)
        if _traced(value):
            origins.setdefault(f"{place}:{name}", provenance.of(value, exchange.seq))

    for exchange in samples:
        for name, value in path_parameters(template, exchange.path):
            note("path_params", name, value, exchange)
        for name, value in exchange.query:
            note("query", name, value, exchange)
        encoding, fields = request_body_fields(exchange)
        encodings.add(encoding)
        for name, value in fields:
            note("body", name, value, exchange)
            if name not in body_order:
                body_order.append(name)
        for name, value in exchange.request_headers:
            if name.lower() not in _TRANSPORT_HEADERS and not name.lower().startswith("sec-"):
                note("header", name, value, exchange)
        for cookie in _cookie_names(exchange):
            cookies[cookie] += 1

    total = len(samples)
    operation: dict[str, Any] = {
        "id": f"{method} {host}{template}",
        "method": method,
        "host": host,
        "path": template,
        "exchanges": [exchange.seq for exchange in samples],
        "body_encoding": sorted(encodings - {"none"}) or ["none"],
    }
    for place, parameters in groups.items():
        if parameters:
            operation[place] = [item.describe(total) for item in parameters.values()]
    if body_order:
        operation["body_order"] = body_order
    if cookies:
        operation["cookies_sent"] = sorted(cookies)
    duplicates = _duplicates(groups)
    if duplicates:
        operation["same_value"] = duplicates
    if origins:
        operation["value_origins"] = origins
    operation["responses"] = response_variants(samples)
    paging = [
        item["name"]
        for item in operation.get("query", []) + operation.get("body", [])
        if item.get("hint") == "pagination"
    ]
    if paging:
        operation["pagination_hint"] = {
            "request": paging,
            "response": sorted(_paging_keys(operation["responses"])),
        }
    actions = sorted({str(e.meta["action"]) for e in samples if e.meta.get("action")})
    if actions:
        operation["caused_by"] = actions
    return operation


def _paging_keys(variants: Sequence[dict[str, Any]]) -> set[str]:
    found: set[str] = set()

    def walk(schema: dict[str, Any], prefix: str, depth: int) -> None:
        for name, item in schema.get("properties", {}).items():
            path = f"{prefix}{name}"
            if _PAGING_RESPONSE.search(name) and "object" not in item["type"]:
                found.add(path)
            if depth < 2:
                walk(item, f"{path}.", depth + 1)

    for variant in variants:
        if "json_schema" in variant:
            walk(variant["json_schema"], "", 0)
    return found


# --- whole-capture facts ------------------------------------------------------------------


def redirect_chains(exchanges: Sequence[Exchange]) -> list[list[dict[str, Any]]]:
    """Link each redirect to the request that followed it."""
    chains: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    expected: str | None = None
    for exchange in exchanges:
        follows = expected is not None and exchange.url.rstrip("/") == expected.rstrip("/")
        if not follows:
            if len(current) > 1:
                chains.append(current)
            current = []
        target, how = _location(exchange), "location"
        if not (_REDIRECT_LOW <= exchange.status <= _REDIRECT_HIGH and target):
            target, how = None, "meta-refresh"
            if "html" in media_type(exchange.response_headers):
                target = html_facts(exchange).get("meta_refresh")
        if follows or target:
            step: dict[str, Any] = {
                "exchange": exchange.seq,
                "status": exchange.status,
                "url": _location_shape(exchange.url),
            }
            if target:
                step["then"] = how
            current.append(step)
        expected = target
    if len(current) > 1:
        chains.append(current)
    return chains


def cookie_table(exchanges: Sequence[Exchange]) -> list[dict[str, Any]]:
    """List every cookie: who set it, for which domain, and which hosts received it."""
    table: dict[str, dict[str, Any]] = {}
    for exchange in exchanges:
        for value in headers_named(exchange.response_headers, "set-cookie"):
            head, *attributes = value.split(";")
            name = head.split("=", 1)[0].strip()
            entry = table.setdefault(
                name, {"name": name, "set_by": [], "domains": [], "flags": [], "sent_to": []}
            )
            entry["set_by"].append(exchange.seq)
            for attribute in attributes:
                key, _, item = attribute.strip().partition("=")
                if key.lower() == "domain" and item not in entry["domains"]:
                    entry["domains"].append(item)
                elif key.lower() in {"httponly", "secure"} and key not in entry["flags"]:
                    entry["flags"].append(key)
            if not any(a.strip().lower().startswith("domain=") for a in attributes):
                host_only = f"{exchange.host} (host-only)"
                if host_only not in entry["domains"]:
                    entry["domains"].append(host_only)
        for name in _cookie_names(exchange):
            entry = table.setdefault(
                name, {"name": name, "set_by": [], "domains": [], "flags": [], "sent_to": []}
            )
            if exchange.host not in entry["sent_to"]:
                entry["sent_to"].append(exchange.host)
    for entry in table.values():
        if not entry["set_by"]:
            entry["note"] = "never set by a recorded response: a page script or an older session"
    return list(table.values())


def envelopes(operations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find top-level keys shared by the JSON object responses of a host."""
    by_host: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for operation in operations:
        for variant in operation["responses"]:
            schema = variant.get("json_schema")
            if schema and "object" in schema["type"] and schema.get("properties"):
                by_host[operation["host"]].append((operation["id"], variant))
    found = []
    for host, members in by_host.items():
        # One odd endpoint (a status file) must not hide the envelope of all the others.
        counts: dict[str, int] = defaultdict(int)
        for _, variant in members:
            for key in variant["json_schema"]["properties"]:
                counts[key] += 1
        shared = {key for key, count in counts.items() if count >= 0.8 * len(members)}
        if not shared:
            continue
        marks: dict[str, list[Any]] = defaultdict(list)
        for _, variant in members:
            for name, value in variant.get("body_marks", {}).items():
                if name in shared and value not in marks[name]:
                    marks[name].append(value)
        operations_seen = sorted({identifier for identifier, _ in members})
        found.append(
            {
                "host": host,
                "keys": sorted(shared),
                "marks": dict(marks),
                "operations": operations_seen,
                "confidence": "several operations" if len(operations_seen) > 1 else "one operation",
            }
        )
    return found


def out_of_scope(capture: Capture, operations: Sequence[dict[str, Any]]) -> list[str]:
    """Name what the capture shows and this skill does not solve."""
    signals: list[str] = []
    sockets = [e.seq for e in capture.exchanges if e.status == 101]
    if sockets or any(event.get("kind") == "websocket" for event in capture.timeline):
        signals.append("WebSocket traffic was recorded: not covered by this skill")
    signals.extend(
        f"#{exchange.seq}: the response carries anti-bot or captcha markers"
        for exchange in capture.exchanges
        if _CHALLENGE.search(exchange.response_body) and noise_reason(exchange) is None
    )
    for operation in operations:
        for place in ("query", "body", "header"):
            for item in operation.get(place, []):
                origin = operation.get("value_origins", {}).get(f"{place}:{item['name']}", {})
                if item.get("hint") == "signature":
                    signals.append(
                        f"{operation['id']}: {place} {item['name']!r} looks like a signature"
                    )
                elif item["looks_like"] == ["uuid"]:
                    continue  # a request id the client makes up: an Inject with a generator
                elif origin.get("from") == "unknown" and item["behaviour"] != "constant":
                    signals.append(
                        f"{operation['id']}: {place} {item['name']!r} changes and comes from no "
                        "response: computed by a page script, or typed by the user"
                    )
        if any(e not in {"none", "form", "json", "multipart"} for e in operation["body_encoding"]):
            signals.append(f"{operation['id']}: request body is {operation['body_encoding']}")
    return list(dict.fromkeys(signals))


def _target_name(target: dict[str, Any]) -> str:
    """Name what was acted on without quoting the page's content.

    The text of a control is its name. The text of anything else (a row of a mail list, a
    whole form) is content of the user's account and stays out of the inventory.
    """
    label = str(target.get("label") or "")
    control = target.get("tag") in _CONTROL_TAGS or bool(target.get("role"))
    if label and len(label) <= _MAX_LABEL and (control or target.get("tag") == "a"):
        return label
    return str(target.get("name") or target.get("selector") or target.get("tag") or "")


def page_facts(event: dict[str, Any]) -> dict[str, Any]:
    """Reduce a page snapshot to what names an outcome.

    A few lines that appeared after an action are a message. Many lines are a list that was
    rendered, content of the account: they are counted, not quoted.
    """
    facts: dict[str, Any] = {}
    for key in ("title", "heading"):
        if event.get(key):
            facts[key] = _shown(str(event[key]), 80)
    notices = [_shown(str(item), 200) for item in event.get("notices") or []][:5]
    if notices:
        facts["notices"] = notices
    appeared = [str(item) for item in event.get("appeared") or []]
    total = int(event.get("appeared_total") or len(appeared))
    if 0 < total <= _MAX_MESSAGE_LINES:
        facts["appeared"] = [_shown(line, 200) for line in appeared]
    elif total:
        facts["appeared_lines"] = total
    return facts


def pages_after(capture: Capture, kept: set[int]) -> dict[int, dict[str, Any]]:
    """Tell, for an exchange an action caused, what the page showed afterwards."""
    shown: dict[int, dict[str, Any]] = {}
    waiting: dict[str, list[int]] = defaultdict(list)
    for event in capture.timeline:
        kind, tab = event.get("kind"), str(event.get("tab", ""))
        if kind == "action":
            waiting[tab] = []
        elif kind == "request" and event.get("exchange") in kept:
            waiting[tab].append(int(event["exchange"]))
        elif kind == "page":
            facts = page_facts(event)
            if any(key in facts for key in ("notices", "appeared")):
                for seq in waiting[tab]:
                    shown.setdefault(seq, facts)
            waiting[tab] = []
    return shown


def _scenario(name: str) -> dict[str, Any]:
    return {"name": name, "actions": [], "exchanges": [], "steps": []}


def scenarios(capture: Capture, kept: set[int]) -> list[dict[str, Any]]:
    """Cut the timeline into scenarios at the user's marks.

    ``steps`` is the scenario in order: what the person did, which exchanges followed and what
    the page then showed.
    """
    result: list[dict[str, Any]] = []
    current = _scenario("(before the first mark)")
    marked = False
    for event in capture.timeline:
        kind = event.get("kind")
        if kind == "mark":
            if current["actions"] or current["exchanges"]:
                result.append(current)
            current = _scenario(str(event.get("label", "")))
            marked = True
        elif kind == "action":
            target = event.get("target") or {}
            did = f"{event.get('action', 'action')} {_target_name(target)}".strip()
            current["actions"].append(did)
            current["steps"].append(did)
        elif kind == "navigation":
            went = f"navigate {_location_shape(str(event.get('url', '')))}"
            current["actions"].append(went)
            current["steps"].append(went)
        elif kind == "request" and event.get("exchange") in kept:
            current["exchanges"].append(event["exchange"])
            current["steps"].append(f"-> #{event['exchange']}")
        elif kind == "page":
            facts = page_facts(event)
            told = facts.get("notices") or facts.get("appeared")
            step = "the page shows: " + " | ".join(told or [])
            # The page is looked at twice after an action: the same text is one step.
            if told and step not in current["steps"][-2:]:
                current["steps"].append(step)
    if current["actions"] or current["exchanges"]:
        result.append(current)
    if not marked and len(result) == 1:
        result[0]["name"] = "(the whole capture: no marks)"
    return result


def build(
    capture: Capture, typed_kinds: frozenset[str] = frozenset({"password", "typed"})
) -> dict[str, Any]:
    """Build the inventory of a redacted capture."""
    noise: list[dict[str, Any]] = []
    api: list[Exchange] = []
    for exchange in capture.exchanges:
        reason = noise_reason(exchange)
        if reason is None:
            api.append(exchange)
        else:
            noise.append({"exchange": exchange.seq, "reason": reason})
    raw_keys = {(e.method, e.host, path_template(e.path)) for e in api}
    templates = merge_templates(raw_keys)
    grouped: dict[tuple[str, str, str], list[Exchange]] = defaultdict(list)
    for exchange in api:
        key = (exchange.method, exchange.host, path_template(exchange.path))
        grouped[exchange.method, exchange.host, templates[key]].append(exchange)
    provenance = Provenance(capture, typed_kinds)
    shown_after = pages_after(capture, {exchange.seq for exchange in api})
    operations = [
        describe_operation(method, host, template, samples, provenance)
        for (method, host, template), samples in grouped.items()
    ]
    for operation in operations:
        for variant in operation["responses"]:
            pages = [shown_after[seq] for seq in variant["exchanges"] if seq in shown_after]
            if pages:
                # What the person saw after this outcome: the site's own name for it.
                distinct = dict.fromkeys(json.dumps(page, sort_keys=True) for page in pages)
                variant["page_showed"] = [json.loads(text) for text in distinct]
    hosts: dict[str, dict[str, Any]] = {}
    for exchange in api:
        entry = hosts.setdefault(exchange.host, {"host": exchange.host, "exchanges": 0})
        entry["exchanges"] += 1
    noise_by_reason: dict[str, list[int]] = defaultdict(list)
    for item in noise:
        noise_by_reason[item["reason"]].append(item["exchange"])
    return {
        "format": INVENTORY_FORMAT,
        "source": {"kind": capture.source, "exchanges": len(capture.exchanges)},
        "warnings": capture.warnings,
        "hosts": list(hosts.values()),
        "scenarios": scenarios(capture, {exchange.seq for exchange in api}),
        "operations": operations,
        "redirect_chains": redirect_chains(api),
        "cookies": cookie_table(api),
        "envelopes": envelopes(operations),
        "noise": dict(noise_by_reason),
        "out_of_scope": out_of_scope(capture, operations),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Write ``inventory.json`` and ``inventory.md`` into a work directory."""
    from inventory_render import render

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workdir", type=Path)
    args = parser.parse_args(argv)
    capture = load(args.workdir / "capture.redacted.json")
    if not capture.redacted:
        print("refusing to read a capture that was not redacted", file=sys.stderr)
        return 1
    inventory = build(capture)
    (args.workdir / "inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    (args.workdir / "inventory.md").write_text(render(inventory), encoding="utf-8", newline="\n")
    print(
        f"operations: {len(inventory['operations'])}, hosts: {len(inventory['hosts'])}, "
        f"noise: {sum(len(v) for v in inventory['noise'].values())}, "
        f"out-of-scope signals: {len(inventory['out_of_scope'])}"
    )
    print(f"written: {args.workdir / 'inventory.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
