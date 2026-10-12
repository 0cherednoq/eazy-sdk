"""Replace the secrets of a capture with stable placeholders.

    python -I capture_redact.py WORKDIR [--typed RECORDING/secrets.local.json]

Reads ``WORKDIR/capture.json`` and writes ``capture.redacted.json`` next to it, with every
secret value replaced by ``__SECRET_<kind>_<n>__``, and ``secrets.json`` that maps the
placeholders back. One value gets one placeholder everywhere it occurs: in a URL, a header,
a form and inside an HTML or JSON body. That is what lets the inventory say where a token
of a request came from.

The model reads only the redacted copy. ``capture.json`` and ``secrets.json`` stay local: the
work directory ignores itself for git.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_model import (
    Capture,
    Exchange,
    dump,
    headers_named,
    is_textual,
    load,
    media_type,
    text_of,
)

SECRETS_FORMAT = "eazy-secrets/1"
PLACEHOLDER = re.compile(r"__SECRET_[a-z0-9]+_\d+__")
PLACEHOLDER_BYTES = re.compile(rb"__SECRET_[a-z0-9]+_\d+__")

_SECRET_NAME = re.compile(
    r"pass|pwd|token|csrf|xsrf|secret|session|sess_?id|(^|[_-])sid$|auth|otp|signature|"
    r"(^|[_-])sign$|api[_-]?key|access[_-]?key|bearer|jwt|credential|(^|[_-])code$",
    re.IGNORECASE,
)
_IDENTITY_NAME = re.compile(r"^(login|user(name)?|e-?mail|phone|account|msisdn)$", re.IGNORECASE)
_EMAIL = re.compile(r"^[\w.+-]{1,64}@[\w-]+(\.[\w-]+)+$")
_ASSIGNMENT = re.compile(
    r"""["']?([A-Za-z_][\w-]{2,40})["']?\s*[:=]\s*["']([^"'\\\s<>]{6,512})["']"""
)
_OPAQUE = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[^\s/]{16,}$")
_MIN_LENGTH = 6
_MIN_TYPED_LENGTH = 4
_MIN_LOCAL_PART = 5


@dataclass(slots=True)
class Secret:
    """One secret value and where it was seen."""

    placeholder: str
    kind: str
    value: str
    origins: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Vault:
    """Secret values in the order they first appeared."""

    by_value: dict[str, Secret] = field(default_factory=dict)
    # Addresses the client itself sent or was redirected with: the account, not its mail.
    identities: set[str] = field(default_factory=set)
    counters: Counter[str] = field(default_factory=Counter)
    skipped: list[str] = field(default_factory=list)

    def add(self, value: str, kind: str, origin: str, *, minimum: int = _MIN_LENGTH) -> None:
        """Register a value; a value already known keeps its first placeholder."""
        value = value.strip()
        if PLACEHOLDER.fullmatch(value):
            return
        if len(value) < minimum or value.lower() in {"true", "false", "null", "undefined"}:
            if value:
                self.skipped.append(f"{origin} ({kind}, {len(value)} chars)")
            return
        known = self.by_value.get(value)
        if known is None:
            slug = re.sub(r"[^a-z0-9]", "", kind.lower()) or "value"
            self.counters[slug] += 1
            known = Secret(f"__SECRET_{slug}_{self.counters[slug]}__", slug, value)
            self.by_value[value] = known
        if origin not in known.origins and len(known.origins) < 5:
            known.origins.append(origin)


def _kind_of(name: str, value: str) -> str | None:
    if _EMAIL.match(value):
        return "email"
    if _SECRET_NAME.search(name):
        return name
    if _IDENTITY_NAME.match(name):
        return "login"
    return None


def _pairs(vault: Vault, pairs: Iterable[tuple[str, str]], origin: str) -> None:
    for name, value in pairs:
        kind = _kind_of(name, value)
        if kind is not None:
            vault.add(value, kind, f"{origin} {name}")
            if kind == "email":
                vault.identities.add(value.strip())


def _served_pairs(vault: Vault, pairs: Iterable[tuple[str, str]], origin: str) -> None:
    """Register secrets a response body carries.

    A page config is full of names like ``authUrl``. Only an address or an opaque value
    (long, letters mixed with digits, not a path) under a secret-like name counts.
    """
    for name, value in pairs:
        if _EMAIL.match(value):
            vault.add(value, "email", f"{origin} {name}")
        elif _SECRET_NAME.search(name) and _OPAQUE.match(value):
            vault.add(value, name, f"{origin} {name}")


def _json_leaves(value: Any, key: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _json_leaves(item, str(name))
    elif isinstance(value, list):
        for item in value:
            yield from _json_leaves(item, key)
    elif isinstance(value, str) and key:
        yield key, value


def _body_pairs(headers: list[tuple[str, str]], body: bytes) -> Iterator[tuple[str, str]]:
    kind = media_type(headers)
    text = text_of(headers, body)
    if kind == "application/x-www-form-urlencoded":
        yield from parse_qsl(text, keep_blank_values=True)
    elif "json" in kind:
        try:
            yield from _json_leaves(json.loads(text))
        except ValueError:
            return
    elif is_textual(headers):
        # A token baked into a page script: "token":"…" or token = '…'.
        for match in _ASSIGNMENT.finditer(text):
            yield match.group(1), match.group(2)


def _cookie_pairs(header_value: str) -> Iterator[tuple[str, str]]:
    for part in header_value.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator:
            yield name, value.strip('"')


def _authorization(vault: Vault, value: str, origin: str) -> None:
    scheme, _, credentials = value.partition(" ")
    vault.add(credentials or scheme, "authorization", origin)
    if scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return
        user, _, password = decoded.partition(":")
        vault.add(user, "login", origin, minimum=_MIN_TYPED_LENGTH)
        vault.add(password, "password", origin, minimum=_MIN_TYPED_LENGTH)


def _collect_exchange(vault: Vault, exchange: Exchange) -> None:
    origin = f"#{exchange.seq} request"
    _pairs(vault, exchange.query, f"{origin} query")
    for name, value in exchange.request_headers:
        low = name.lower()
        if low == "cookie":
            for cookie, cookie_value in _cookie_pairs(value):
                vault.add(cookie_value, f"cookie{cookie}", f"{origin} cookie {cookie}")
        elif low in {"authorization", "proxy-authorization"}:
            _authorization(vault, value, f"{origin} {name}")
        elif _SECRET_NAME.search(name):
            vault.add(value, name, f"{origin} header {name}")
    _pairs(vault, _body_pairs(exchange.request_headers, exchange.request_body), f"{origin} body")
    origin = f"#{exchange.seq} response"
    for value in headers_named(exchange.response_headers, "set-cookie"):
        cookie, _, rest = value.partition("=")
        cookie_value = rest.split(";", 1)[0].strip('"')
        vault.add(cookie_value, f"cookie{cookie.strip()}", f"{origin} set-cookie {cookie.strip()}")
    location = exchange.response_header("location")
    if location:
        _pairs(
            vault,
            parse_qsl(urlsplit(location).query, keep_blank_values=True),
            f"{origin} location",
        )
    for name, value in exchange.response_headers:
        if name.lower() != "set-cookie" and _SECRET_NAME.search(name):
            vault.add(value, name, f"{origin} header {name}")
    _served_pairs(
        vault, _body_pairs(exchange.response_headers, exchange.response_body), f"{origin} body"
    )


def _derive_from_emails(vault: Vault, hosts: set[str]) -> None:
    """Register the halves of an account address: it also travels as a login and a domain."""
    for secret in [item for item in vault.by_value.values() if item.value in vault.identities]:
        local, _, domain = secret.value.partition("@")
        if len(local) >= _MIN_LOCAL_PART:
            vault.add(local, "emaillocal", f"local part of {secret.placeholder}")
        # A mailbox on the site's own domain would otherwise erase the hosts of every URL.
        if not any(host == domain or host.endswith("." + domain) for host in hosts):
            vault.add(domain, "emaildomain", f"domain of {secret.placeholder}")


def collect(capture: Capture, typed: Sequence[dict[str, Any]] = ()) -> Vault:
    """Find every secret value of a capture."""
    vault = Vault()
    for entry in typed:
        described = entry.get("field") or {}
        kind = "password" if described.get("type") == "password" else "typed"
        label = described.get("name") or described.get("label") or "field"
        value = str(entry.get("value", ""))
        vault.add(value, kind, f"typed into {label}", minimum=_MIN_TYPED_LENGTH)
        if _EMAIL.match(value.strip()):
            vault.identities.add(value.strip())
    for exchange in capture.exchanges:
        _collect_exchange(vault, exchange)
    _derive_from_emails(vault, {exchange.host.split(":")[0] for exchange in capture.exchanges})
    return vault


def _variants(value: str) -> set[str]:
    encoded = {value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1]}
    encoded |= {re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), item) for item in encoded}
    return {item for item in encoded if item}


def build_pattern(vault: Vault) -> tuple[re.Pattern[bytes], dict[bytes, bytes]] | None:
    """Compile one pattern over every spelling of every secret, longest first."""
    table: dict[bytes, bytes] = {}
    for secret in vault.by_value.values():
        for variant in _variants(secret.value):
            table.setdefault(variant.encode("utf-8"), secret.placeholder.encode("ascii"))
    if not table:
        return None
    ordered = sorted(table, key=len, reverse=True)
    return re.compile(b"|".join(re.escape(item) for item in ordered)), table


def redact(capture: Capture, vault: Vault) -> Capture:
    """Return the capture with every secret replaced; the argument is left as it was."""
    compiled = build_pattern(vault)
    out = Capture(
        source=capture.source,
        files=list(capture.files),
        timeline=json.loads(json.dumps(capture.timeline)),
        warnings=list(capture.warnings),
        redacted=True,
    )
    if compiled is None:
        out.exchanges = list(capture.exchanges)
        return out
    pattern, table = compiled

    def clean_bytes(data: bytes) -> bytes:
        return pattern.sub(lambda match: table[match.group(0)], data)

    def clean(text: str) -> str:
        return clean_bytes(text.encode("utf-8")).decode("utf-8")

    for item in capture.exchanges:
        out.exchanges.append(
            Exchange(
                seq=item.seq,
                method=item.method,
                url=clean(item.url),
                request_headers=[(name, clean(value)) for name, value in item.request_headers],
                request_body=clean_bytes(item.request_body),
                status=item.status,
                response_headers=[(name, clean(value)) for name, value in item.response_headers],
                response_body=clean_bytes(item.response_body),
                time=item.time,
                meta=json.loads(clean(json.dumps(item.meta, ensure_ascii=False))),
                notes=list(item.notes),
            )
        )
    out.timeline = json.loads(clean(json.dumps(out.timeline, ensure_ascii=False)))
    return out


def secrets_document(vault: Vault) -> dict[str, Any]:
    """Describe the vault for ``secrets.json``."""
    return {
        "format": SECRETS_FORMAT,
        "secrets": {
            secret.placeholder: {
                "kind": secret.kind,
                "value": secret.value,
                "origins": secret.origins,
            }
            for secret in vault.by_value.values()
        },
    }


def leaks(capture: Capture, vault: Vault) -> list[str]:
    """List secrets still present in a redacted capture; an empty list is the pass."""
    found: list[str] = []
    for exchange in capture.exchanges:
        parts = [exchange.url.encode(), exchange.request_body, exchange.response_body]
        parts += [value.encode() for _, value in exchange.request_headers]
        parts += [value.encode() for _, value in exchange.response_headers]
        # A placeholder may spell a secret itself: "password" inside __SECRET_password_1__.
        blob = PLACEHOLDER_BYTES.sub(b"", b"\n".join(parts))
        found += [
            f"#{exchange.seq}: {secret.placeholder}"
            for secret in vault.by_value.values()
            if secret.value.encode() in blob
        ]
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """Redact ``capture.json`` of a work directory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--typed", type=Path, help="secrets.local.json of a recording")
    args = parser.parse_args(argv)
    capture = load(args.workdir / "capture.json")
    typed: list[dict[str, Any]] = []
    if args.typed is not None:
        typed = list(json.loads(args.typed.read_text(encoding="utf-8")).get("typed", []))
    vault = collect(capture, typed)
    redacted = redact(capture, vault)
    remaining = leaks(redacted, vault)
    if remaining:
        print("secrets survived the redaction:\n  " + "\n  ".join(remaining), file=sys.stderr)
        return 1
    (args.workdir / ".gitignore").write_text("*\n", encoding="utf-8")
    dump(redacted, args.workdir / "capture.redacted.json")
    (args.workdir / "secrets.json").write_text(
        json.dumps(secrets_document(vault), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    kinds = Counter(secret.kind for secret in vault.by_value.values())
    print(f"secrets: {len(vault.by_value)} values replaced")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind}: {count}")
    if vault.skipped:
        print(f"left as they are, too short to be a secret: {len(vault.skipped)} values")
    print(f"written: {args.workdir / 'capture.redacted.json'} (the model reads only this one)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
