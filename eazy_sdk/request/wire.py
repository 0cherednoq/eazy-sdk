"""How one request looks on the wire: everything that decides its bytes.

Serialization is four transforms with different owners — model to structure, structure to
bytes, public schema to wire schema, bytes to bytes on the wire. The first has an
implementation half that changes nothing about the result (which library reads the model),
and that half lives in :class:`~eazy_sdk.serialization.Serialization` on the SDK root. The
rest decides what the server receives, so it is contract, and it is declared here.

The rule that separates the two: **what changes the bytes is contract, what does not is
implementation.** It matters because a signature is computed over the bytes that leave. A
policy that claims to change them and does not is worse than no policy at all — which is
exactly what the six unrelated meanings of "wire" had become.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, runtime_checkable

type TransportProtocol = Literal["http/1.1", "http/2", "http/3"]


@dataclass(frozen=True, slots=True)
class JsonPolicy:
    """How a JSON structure becomes bytes — the same bytes the signature reads."""

    ensure_ascii: bool = False
    separators: tuple[str, str] = (",", ":")
    sort_keys: bool = False

    def __post_init__(self) -> None:
        if len(self.separators) != 2:
            raise ValueError("separators is an (item, key) pair")


@dataclass(frozen=True, slots=True)
class QueryCodec:
    """How query values become percent-encoded bytes, symmetric to the body codec."""

    space: Literal["percent", "plus"] = "percent"
    percent_uppercase: bool = True


@dataclass(frozen=True, slots=True)
class FieldOrder:
    """The order fields take on the wire, where the server is known to care."""

    query: tuple[str, ...] | None = None
    header: tuple[str, ...] | None = None
    cookie: tuple[str, ...] | None = None
    body: tuple[str, ...] | None = None


DEFAULT_JSON_POLICY = JsonPolicy()
DEFAULT_QUERY_CODEC = QueryCodec()


@dataclass(frozen=True, slots=True)
class Wire:
    """One declaration of a request's representation, inherited operation → router → mixin.

    Every field is optional and ``None`` means "inherit": an operation that declares only a
    field order keeps the encryption its service declared. Nothing here is decoration — each
    field reaches the bytes, and a test walks them to prove it.
    """

    encrypted: object | None = None
    order: FieldOrder | None = None
    exact: bool | None = None
    encoding: JsonPolicy | None = None
    query: QueryCodec | None = None
    transport: TransportProtocol | None = None
    """The protocol the transport must speak; checked against the handler profile."""

    def over(self, base: Wire | None) -> Wire:
        """Merge this declaration over a less specific one, field by field."""

        if base is None:
            return self
        return replace(
            base,
            **{
                name: value
                for name in (
                    "encrypted",
                    "order",
                    "exact",
                    "encoding",
                    "query",
                    "transport",
                )
                if (value := getattr(self, name)) is not None
            },
        )

    @property
    def json_policy(self) -> JsonPolicy:
        return self.encoding if self.encoding is not None else DEFAULT_JSON_POLICY

    @property
    def query_codec(self) -> QueryCodec:
        return self.query if self.query is not None else DEFAULT_QUERY_CODEC

    @property
    def is_exact(self) -> bool:
        return bool(self.exact)


EMPTY_WIRE = Wire()


def dump_json(
    value: object,
    policy: JsonPolicy = DEFAULT_JSON_POLICY,
    *,
    default: Callable[[Any], Any] | None = None,
    sort_keys: bool | None = None,
) -> bytes:
    """Encode a JSON structure to the bytes the wire carries — the only place that decides.

    Every site that used to hardcode ``ensure_ascii=False, separators=(",", ":")`` calls
    this, so the body, the signature base and the encrypted payload cannot drift apart.
    ``sort_keys`` is an override for the two callers whose contract is canonical ordering.
    """

    return json.dumps(
        value,
        ensure_ascii=policy.ensure_ascii,
        allow_nan=False,
        separators=policy.separators,
        sort_keys=policy.sort_keys if sort_keys is None else sort_keys,
        default=default,
    ).encode("utf-8")


@runtime_checkable
class JsonBackend(Protocol):
    """The library that turns a JSON structure into bytes — implementation, not contract.

    Which backend runs is declared once on the SDK root; *what bytes it must produce* is the
    operation's :class:`JsonPolicy`. A backend that cannot honour a policy says so through
    :meth:`supports`, and the operation is rejected at compile time rather than signed over
    bytes the server never agreed to.

    ``readable`` is a separate question from writing: a backend that encodes correctly can
    still decode lossily. ``orjson``, for instance, represents an integer wider than 64 bits
    as a Python ``float`` rather than raising or keeping its precision, and would do so
    silently for every response it read. A backend declares ``readable = False`` to say "do
    not use me to parse a response" without needing to change how it encodes requests.
    """

    @property
    def name(self) -> str: ...

    @property
    def readable(self) -> bool: ...

    def supports(self, policy: JsonPolicy) -> bool: ...

    def dumps(
        self,
        value: object,
        policy: JsonPolicy,
        *,
        default: Callable[[Any], Any] | None = None,
        sort_keys: bool | None = None,
    ) -> bytes: ...

    def loads(self, data: bytes) -> object: ...


@dataclass(frozen=True, slots=True)
class StdlibJson:
    """The standard library encoder, which can satisfy every policy this SDK can express."""

    name: str = "json"
    readable: bool = True
    """``json.loads`` keeps arbitrary-precision integers exactly; always safe to read with."""

    def supports(self, policy: JsonPolicy) -> bool:
        return True

    def dumps(
        self,
        value: object,
        policy: JsonPolicy,
        *,
        default: Callable[[Any], Any] | None = None,
        sort_keys: bool | None = None,
    ) -> bytes:
        return dump_json(value, policy, default=default, sort_keys=sort_keys)

    def loads(self, data: bytes) -> object:
        return json.loads(data)


DEFAULT_JSON_BACKEND: JsonBackend = StdlibJson()


def encode_query_component(encoded: str, codec: QueryCodec) -> str:
    """Apply the declared query encoding to one already percent-encoded component."""

    if not codec.percent_uppercase:
        encoded = _lower_percent_escapes(encoded)
    if codec.space == "plus":
        encoded = encoded.replace("%20" if codec.percent_uppercase else "%20".lower(), "+")
    return encoded


def _lower_percent_escapes(value: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "%" and index + 2 < len(value) + 1:
            out.append("%" + value[index + 1 : index + 3].lower())
            index += 3
            continue
        out.append(char)
        index += 1
    return "".join(out)


__all__ = [
    "DEFAULT_JSON_BACKEND",
    "FieldOrder",
    "JsonBackend",
    "JsonPolicy",
    "QueryCodec",
    "StdlibJson",
    "TransportProtocol",
    "Wire",
    "dump_json",
]
