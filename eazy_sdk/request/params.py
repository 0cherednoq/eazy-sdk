"""Ordered, multi-value query parameters and OpenAPI serialization helpers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from eazy_sdk.codecs import ScalarCodec
from eazy_sdk.exceptions import ParameterSerializationError
from eazy_sdk.sentinels import Unset

_UNRESERVED = "-._~"
_RESERVED = ":/?#[]@!$&'()*+,;="


@dataclass(frozen=True)
class _QueryItem:
    name: str
    value: str
    allow_reserved: bool = False
    name_safe: str = ""


class QueryParams(MutableMapping[str, Any]):
    """An ordered query multimap that preserves duplicate keys on the wire."""

    def __init__(
        self,
        values: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
    ) -> None:
        self._items: list[_QueryItem] = []
        self._raw: str | None = None
        if values is not None:
            items = values.items() if isinstance(values, Mapping) else values
            for name, value in items:
                self.add(name, value)

    def add(
        self,
        name: str,
        value: Any,
        *,
        allow_reserved: bool = False,
        name_safe: str = "",
    ) -> None:
        """Append one value without replacing earlier values for ``name``."""
        if self._raw is not None:
            raise ParameterSerializationError(
                "individual query parameters cannot be combined with a querystring parameter"
            )
        self._items.append(
            _QueryItem(name, _primitive(value), allow_reserved=allow_reserved, name_safe=name_safe)
        )

    def extend(self, values: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> None:
        """Append all values in their input order."""
        items = values.items() if isinstance(values, Mapping) else values
        for name, value in items:
            self.add(name, value)

    def extend_query(self, values: QueryParams) -> None:
        """Append another multimap while preserving its encoding metadata."""
        if values._raw is not None:
            self.set_raw(values._raw)
            return
        self._items.extend(values._items)

    def set_raw(self, value: str) -> None:
        """Set an already serialized whole query string (OpenAPI 3.2)."""
        if self._items or self._raw is not None:
            raise ParameterSerializationError(
                "a querystring parameter cannot be combined with other query values"
            )
        self._raw = value.removeprefix("?")

    def getall(self, name: str) -> tuple[str, ...]:
        """Return every value stored under ``name`` in wire order."""
        return tuple(item.value for item in self._items if item.name == name)

    def multi_items(self) -> tuple[tuple[str, str], ...]:
        """Return all key/value pairs, including duplicate keys."""
        return tuple((item.name, item.value) for item in self._items)

    def encoded_items(self) -> tuple[tuple[str, str, str, str], ...]:
        """Return raw and encoded pairs without discarding per-item encoding policy."""
        output: list[tuple[str, str, str, str]] = []
        for item in self._items:
            name = quote(item.name, safe=_UNRESERVED + item.name_safe)
            safe = _UNRESERVED + (_RESERVED if item.allow_reserved else "")
            value = quote(item.value, safe=safe)
            output.append((item.name, item.value, name, value))
        return tuple(output)

    @property
    def raw_query(self) -> str | None:
        """Return the lossless whole-query representation, when one was supplied."""
        return self._raw

    def copy(self) -> QueryParams:
        """Return an independent copy."""
        out = QueryParams()
        out._items = list(self._items)
        out._raw = self._raw
        return out

    def __getitem__(self, name: str) -> str:
        for item in reversed(self._items):
            if item.name == name:
                return item.value
        raise KeyError(name)

    def __setitem__(self, name: str, value: Any) -> None:
        self._remove(name)
        self.add(name, value)

    def __delitem__(self, name: str) -> None:
        original = len(self._items)
        self._remove(name)
        if len(self._items) == original:
            raise KeyError(name)

    def _remove(self, name: str) -> None:
        self._items = [item for item in self._items if item.name != name]

    def __iter__(self) -> Iterator[str]:
        seen: set[str] = set()
        for item in self._items:
            if item.name not in seen:
                seen.add(item.name)
                yield item.name

    def __len__(self) -> int:
        return len(set(self))

    def __bool__(self) -> bool:
        return bool(self._items) or self._raw is not None

    def get(self, name: str, default: Any = None) -> Any:
        """Return the last value under ``name`` or ``default``."""
        try:
            return self[name]
        except KeyError:
            return default

    def setdefault(self, name: str, default: Any = None) -> str:
        """Set and return ``default`` only if ``name`` is absent."""
        try:
            return self[name]
        except KeyError:
            self.add(name, default)
            return _primitive(default)

    def render(self) -> str:
        """Render the exact query string, preserving duplicates and reservations."""
        if self._raw is not None:
            return self._raw
        return "&".join(
            f"{encoded_name}={encoded_value}"
            for _, _, encoded_name, encoded_value in self.encoded_items()
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, QueryParams):
            return self._items == other._items
        if isinstance(other, Mapping):
            return dict(self.multi_items()) == dict(other)
        return NotImplemented

    def __repr__(self) -> str:
        return f"QueryParams({self.multi_items()!r})"


def append_query(url: str, params: QueryParams) -> str:
    """Append ``params`` to ``url`` without re-encoding their rendered form."""
    if not params:
        return url
    split = urlsplit(url)
    rendered = params.render()
    query = f"{split.query}&{rendered}" if split.query else rendered
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


@dataclass(frozen=True)
class Path:
    """An OpenAPI path parameter descriptor."""

    name: str | None = None
    style: str = field(default="simple", kw_only=True)
    explode: bool = field(default=False, kw_only=True)
    allow_reserved: bool = field(default=False, kw_only=True)
    codec: ScalarCodec | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.style not in {"simple", "label", "matrix"}:
            raise ValueError(f"unsupported path parameter style: {self.style!r}")


@dataclass(frozen=True)
class Query:
    """An OpenAPI query parameter descriptor."""

    name: str | None = None
    style: str = field(default="form", kw_only=True)
    explode: bool = field(default=True, kw_only=True)
    allow_reserved: bool = field(default=False, kw_only=True)
    codec: ScalarCodec | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.style not in {"form", "spaceDelimited", "pipeDelimited", "deepObject"}:
            raise ValueError(f"unsupported query parameter style: {self.style!r}")


@dataclass(frozen=True)
class QueryString:
    """An OpenAPI 3.2 descriptor for the complete query string."""

    name: str = "querystring"
    content_type: str = field(default="application/x-www-form-urlencoded", kw_only=True)

    def __post_init__(self) -> None:
        if self.content_type not in {"application/x-www-form-urlencoded", "text/plain"}:
            raise ValueError(f"unsupported querystring media type: {self.content_type!r}")


@dataclass(frozen=True)
class Header:
    """An OpenAPI request-header parameter descriptor."""

    name: str | None = None
    style: str = field(default="simple", kw_only=True)
    explode: bool = field(default=False, kw_only=True)
    allow_reserved: bool = field(default=False, kw_only=True)
    codec: ScalarCodec | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.style != "simple":
            raise ValueError("OpenAPI header parameters only support style='simple'")


@dataclass(frozen=True)
class Cookie:
    """An OpenAPI cookie parameter descriptor."""

    name: str | None = None
    style: str = field(default="form", kw_only=True)
    explode: bool = field(default=True, kw_only=True)
    allow_reserved: bool = field(default=False, kw_only=True)
    codec: ScalarCodec | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.style != "form":
            raise ValueError("OpenAPI cookie parameters only support style='form'")


type Parameter = Path | Query | QueryString | Header | Cookie


def _primitive(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return _primitive(value.value)
    return str(value)


def _kind(value: Any) -> str:
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return "array"
    return "primitive"


def _flatten_object(value: Mapping[Any, Any], *, equals: bool) -> list[str]:
    out: list[str] = []
    for key, item in value.items():
        if equals:
            out.append(f"{_primitive(key)}={_primitive(item)}")
        else:
            out.extend((_primitive(key), _primitive(item)))
    return out


def serialize_path(parameter: Path, value: Any) -> str:
    """Serialize one path parameter according to OpenAPI 3.x rules."""
    name = _required_name(parameter.name)
    if isinstance(value, Unset):
        return ""
    kind = _kind(value)
    safe = _UNRESERVED + (_RESERVED if parameter.allow_reserved else "")

    def encoded(item: Any) -> str:
        return quote(_primitive(item), safe=safe)

    if kind == "primitive":
        raw = encoded(value)
    elif kind == "array":
        delimiter = "." if parameter.style == "label" and parameter.explode else ","
        raw = delimiter.join(encoded(item) for item in value)
    else:
        fields = _flatten_object(value, equals=parameter.explode)
        delimiter = "." if parameter.style == "label" and parameter.explode else ","
        raw = delimiter.join(
            encoded(item) if "=" not in item else _encode_pair(item, safe) for item in fields
        )

    if parameter.style == "simple":
        return raw
    if parameter.style == "label":
        return f".{raw}"
    if kind == "array" and parameter.explode:
        return "".join(f";{quote(name, safe=_UNRESERVED)}={encoded(item)}" for item in value)
    if kind == "object" and parameter.explode:
        return "".join(f";{encoded(key)}={encoded(item)}" for key, item in value.items())
    return f";{quote(name, safe=_UNRESERVED)}={raw}"


def _encode_pair(pair: str, safe: str) -> str:
    key, _, value = pair.partition("=")
    return f"{quote(key, safe=safe)}={quote(value, safe=safe)}"


def serialize_query(parameter: Query, value: Any) -> QueryParams:
    """Serialize one query parameter to an ordered multimap."""
    name = _required_name(parameter.name)
    out = QueryParams()
    if isinstance(value, Unset):
        return out
    kind = _kind(value)

    def add(name: str, item: Any, *, name_safe: str = "") -> None:
        out.add(
            name,
            item,
            allow_reserved=parameter.allow_reserved,
            name_safe=name_safe,
        )

    if parameter.style == "deepObject":
        if kind != "object":
            raise ParameterSerializationError(
                f"deepObject query parameter {name!r} requires a mapping"
            )
        for key, item in value.items():
            add(f"{name}[{_primitive(key)}]", item, name_safe="[]")
        return out
    if parameter.style in {"spaceDelimited", "pipeDelimited"}:
        if kind != "array":
            raise ParameterSerializationError(
                f"{parameter.style} query parameter {name!r} requires an array"
            )
        delimiter = " " if parameter.style == "spaceDelimited" else "|"
        add(name, delimiter.join(_primitive(item) for item in value))
        return out
    if kind == "primitive":
        add(name, value)
    elif kind == "array":
        if parameter.explode:
            for item in value:
                add(name, item)
        else:
            add(name, ",".join(_primitive(item) for item in value))
    elif parameter.explode:
        for key, item in value.items():
            add(_primitive(key), item)
    else:
        add(name, ",".join(_flatten_object(value, equals=False)))
    return out


def serialize_querystring(
    parameter: QueryString,
    value: Any,
    *,
    models: Any | None = None,
) -> QueryParams:
    """Serialize the complete OpenAPI 3.2 query-string parameter."""
    out = QueryParams()
    if isinstance(value, Unset):
        return out
    if parameter.content_type == "text/plain":
        if not isinstance(value, str):
            raise ParameterSerializationError("text/plain querystring value must be a string")
        out.set_raw(value)
        return out
    dumped = value if isinstance(value, Mapping) else (models.dump(value) if models else value)
    if not isinstance(dumped, Mapping):
        raise ParameterSerializationError(
            "application/x-www-form-urlencoded querystring value must be a mapping"
        )
    out.set_raw(urlencode(dumped, doseq=True))
    return out


def serialize_header(parameter: Header, value: Any) -> str | None:
    """Serialize one OpenAPI header parameter."""
    if isinstance(value, Unset):
        return None
    kind = _kind(value)
    if kind == "primitive":
        return _primitive(value)
    if kind == "array":
        return ",".join(_primitive(item) for item in value)
    return ",".join(_flatten_object(value, equals=parameter.explode))


def serialize_cookie(parameter: Cookie, value: Any) -> tuple[str, str] | None:
    """Serialize one OpenAPI cookie parameter."""
    name = _required_name(parameter.name)
    if isinstance(value, Unset):
        return None
    kind = _kind(value)
    if kind == "primitive":
        return name, _primitive(value)
    if kind == "array":
        return name, ",".join(_primitive(item) for item in value)
    return name, ",".join(_flatten_object(value, equals=parameter.explode))


def _required_name(name: str | None) -> str:
    if name is None:
        raise ParameterSerializationError("parameter wire name was not resolved")
    return name
