"""Immutable prepared request artifacts and deterministic component codecs."""

from __future__ import annotations

import dataclasses
import gzip
import json
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from io import BytesIO
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, cast
from urllib.parse import quote, unquote_to_bytes, urljoin, urlsplit

from eazy_sdk.codecs import BodyCodec, EncodeContext, ScalarCodec, ScalarEncodeContext
from eazy_sdk.core.errors import BindingError, PlanError
from eazy_sdk.core.http import RequestLocation
from eazy_sdk.core.kernel import OperationValues, ValueSlot
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
from eazy_sdk.request.descriptors import (
    BytesBody,
    Form,
    FormBody,
    JsonBody,
    JsonField,
    MultipartBody,
    MultipartPart,
    Part,
    ReplayableStreamBody,
    WireOptions,
)
from eazy_sdk.request.logical import (
    ExactBodyInput,
    FormInput,
    JsonInput,
    MultipartInput,
    MultipartInputPart,
    NoBodyInput,
    StreamBodyInput,
    ZaprosBodyInput,
)
from eazy_sdk.request.params import (
    Cookie,
    Header,
    Path,
    Query,
    QueryString,
    serialize_cookie,
    serialize_header,
    serialize_path,
    serialize_query,
    serialize_querystring,
)

if TYPE_CHECKING:
    from eazy_sdk.compile.http_compiler import CompiledContract


_NO_BODY_DOCUMENT_OVERRIDE = object()


class HttpProtocol(Enum):
    HTTP_1_1 = "http/1.1"
    HTTP_2 = "http/2"
    HTTP_3 = "http/3"


class ComponentExtrasPolicy(Enum):
    ERROR = "error"
    APPEND = "append"
    PREPEND = "prepend"


@dataclass(frozen=True, slots=True)
class BodyLayout:
    descriptor: object | None
    field_order: tuple[str, ...] | None = None
    slots: tuple[ValueSlot[Any], ...] = ()
    flat: bool = False
    projected: bool = False


@dataclass(frozen=True, slots=True)
class RequestLayout:
    path: tuple[ValueSlot[Any], ...]
    query: tuple[ValueSlot[Any], ...]
    headers: tuple[ValueSlot[Any], ...]
    cookies: tuple[ValueSlot[Any], ...]
    body: BodyLayout
    extras: ComponentExtrasPolicy = ComponentExtrasPolicy.ERROR


@dataclass(frozen=True, slots=True)
class WireProfile:
    protocol: HttpProtocol = HttpProtocol.HTTP_1_1
    exact: bool = False
    query_space: Literal["percent", "plus"] = "percent"
    percent_uppercase: bool = True
    json_ensure_ascii: bool = False
    automatic_fields: tuple[str, ...] = ("Host", "Content-Length", "Content-Type", "Cookie")


@dataclass(frozen=True, slots=True)
class PreparedQueryPair:
    name: bytes
    value: bytes
    encoded_name: bytes
    encoded_value: bytes
    slot: ValueSlot[Any] | None = None

    @property
    def wire(self) -> bytes:
        return self.encoded_name + b"=" + self.encoded_value


@dataclass(frozen=True, slots=True)
class HeaderField:
    name: bytes
    value: bytes
    slot: ValueSlot[Any] | None = None
    sensitive: bool = False


PreparedHeader = HeaderField


@dataclass(frozen=True, slots=True)
class PreparedCookie:
    name: bytes
    value: bytes
    slot: ValueSlot[Any] | None = None
    sensitive: bool = True


@dataclass(frozen=True, slots=True)
class BufferedBody:
    content: bytes
    content_type: bytes | None


@dataclass(frozen=True, slots=True)
class ReplayableBodyStream:
    factory: Callable[[], BinaryIO]
    known_length: int | None


type FrozenJsonValue = (
    None | bool | int | float | str | tuple[object, ...] | tuple[tuple[str, object], ...]
)


@dataclass(frozen=True, slots=True)
class PreparedFormField:
    name: bytes
    value: bytes


@dataclass(frozen=True, slots=True)
class PreparedBodyView:
    content: bytes
    media_type: str | None
    json_view: FrozenJsonValue | None = None
    form_fields: tuple[PreparedFormField, ...] | None = None
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class PreparedRequestView:
    method: bytes
    scheme: bytes
    authority: bytes
    path: bytes
    target: bytes
    query_pairs: tuple[PreparedQueryPair, ...]
    headers: tuple[PreparedHeader, ...]
    cookies: tuple[PreparedCookie, ...]
    body: PreparedBodyView


@dataclass(frozen=True, slots=True)
class ReservedOutput:
    identity: object
    location: RequestLocation
    slot: ValueSlot[Any]


@dataclass(frozen=True, slots=True)
class UnsignedPreparedRequest:
    method: bytes
    scheme: bytes
    authority: bytes
    target: bytes
    headers: tuple[HeaderField, ...]
    body: BufferedBody | ReplayableBodyStream
    protocol: HttpProtocol
    reserved_outputs: tuple[ReservedOutput, ...] = ()
    view: PreparedRequestView | None = None
    body_input: ZaprosBodyInput = dataclasses.field(default_factory=NoBodyInput)

    def finalize(self) -> PreparedRequest:
        if self.reserved_outputs:
            names = ", ".join(item.slot.diagnostic_name for item in self.reserved_outputs)
            raise PlanError(f"required reserved outputs are unfilled: {names}")
        return PreparedRequest(
            method=self.method,
            scheme=self.scheme,
            authority=self.authority,
            target=self.target,
            headers=self.headers,
            body=self.body,
            protocol=self.protocol,
            view=self.view,
            body_input=self.body_input,
        )


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    method: bytes
    scheme: bytes
    authority: bytes
    target: bytes
    headers: tuple[HeaderField, ...]
    body: BufferedBody | ReplayableBodyStream
    protocol: HttpProtocol
    view: PreparedRequestView | None = None
    body_input: ZaprosBodyInput = dataclasses.field(default_factory=NoBodyInput)

    @property
    def url(self) -> str:
        return (
            self.scheme.decode("ascii")
            + "://"
            + self.authority.decode("ascii")
            + self.target.decode("ascii")
        )


@dataclass(frozen=True, slots=True)
class RequestPreparer:
    base_url: str
    profile: WireProfile | None = None
    models: ModelAdapterRegistry = dataclasses.field(default_factory=default_model_adapters)

    def prepare[T](
        self,
        compiled: CompiledContract[T],
        values: OperationValues,
        *,
        reserved_outputs: tuple[ReservedOutput, ...] = (),
        boundary: str | None = None,
        url_override: str | None = None,
        method_override: str | None = None,
        omit_body: bool = False,
        body_document_override: object = _NO_BODY_DOCUMENT_OVERRIDE,
    ) -> UnsignedPreparedRequest:
        layout = compile_layout(compiled)
        profile = self.profile or WireProfile()
        contract = compiled.contract
        url = url_override or _resolve_url(self.base_url, contract.path)
        split = urlsplit(url)
        path = split.path or "/"
        for slot in () if url_override is not None else layout.path:
            value = values.require(slot)
            name = compiled.wire_names[slot]
            marker = "{" + name + "}"
            if marker not in path:
                raise PlanError(f"path slot {name!r} is absent from template {path!r}")
            descriptor = compiled.descriptors[slot]
            if isinstance(descriptor, Path):
                value = _scalar_value(
                    descriptor.codec,
                    value,
                    location="path",
                    wire_name=name,
                    operation_id=contract.operation_id,
                )
                rendered = serialize_path(descriptor, value)
            else:
                rendered = quote(_primitive(value), safe="-._~")
            path = path.replace(marker, rendered)
        generated_pairs, generated_query = _query_parts(
            layout.query,
            values,
            compiled.descriptors,
            compiled.wire_names,
            operation_id=contract.operation_id,
            models=self.models,
        )
        existing_pairs, existing_query = _existing_query(split.query)
        query_pairs = (*existing_pairs, *generated_pairs)
        _ensure_unique_query_pairs(query_pairs)
        query = b"&".join(part for part in (existing_query, generated_query) if part)
        path_bytes = path.encode("ascii")
        target = path_bytes + (b"?" + query if query else b"")
        cookies = _cookies(
            layout.cookies,
            values,
            compiled.descriptors,
            compiled.wire_names,
            operation_id=contract.operation_id,
        )
        body: BufferedBody | ReplayableBodyStream
        if omit_body:
            body = BufferedBody(b"", None)
            body_view = PreparedBodyView(b"", None)
            body_input: ZaprosBodyInput = NoBodyInput()
        else:
            body, body_view, body_input = _body(
                layout.body,
                values,
                boundary=boundary,
                models=self.models,
                descriptors=compiled.descriptors,
                wire_names=compiled.wire_names,
                operation_id=contract.operation_id,
                document_override=body_document_override,
            )
        headers = _headers(
            layout.headers,
            values,
            compiled.descriptors,
            compiled.wire_names,
            operation_id=contract.operation_id,
        )
        if omit_body:
            headers = tuple(
                field
                for field in headers
                if field.name.lower()
                not in {b"content-length", b"content-type", b"transfer-encoding"}
            )
        content_type = (
            body.content_type
            if isinstance(body, BufferedBody)
            else _body_content_type(layout.body.descriptor)
        )
        headers = _automatic_headers(
            headers,
            authority=split.netloc.encode("ascii"),
            body=body,
            content_type=content_type,
            cookies=cookies,
        )
        view = PreparedRequestView(
            method=(method_override or contract.method).upper().encode("ascii"),
            scheme=split.scheme.encode("ascii"),
            authority=split.netloc.encode("ascii"),
            path=path_bytes,
            target=target,
            query_pairs=query_pairs,
            headers=headers,
            cookies=cookies,
            body=body_view,
        )
        return UnsignedPreparedRequest(
            method=view.method,
            scheme=view.scheme,
            authority=view.authority,
            target=view.target,
            headers=headers,
            body=body,
            protocol=profile.protocol,
            reserved_outputs=reserved_outputs,
            view=view,
            body_input=body_input,
        )


def compile_layout[T](compiled: CompiledContract[T]) -> RequestLayout:
    wire = cast(WireOptions | None, getattr(compiled.contract, "wire", None))
    groups = {
        RequestLocation.PATH: tuple(compiled.path_slots.values()),
        RequestLocation.QUERY: tuple(compiled.query_slots.values()),
        RequestLocation.HEADER: tuple(compiled.header_slots.values()),
        RequestLocation.COOKIE: tuple(compiled.cookie_slots.values()),
        RequestLocation.BODY: tuple(compiled.body_slots.values()),
    }
    query = _apply_order(
        groups[RequestLocation.QUERY],
        wire.query_order if wire else None,
        "query",
        compiled.wire_names,
    )
    headers = _apply_order(
        groups[RequestLocation.HEADER],
        wire.header_order if wire else None,
        "header",
        compiled.wire_names,
    )
    cookies = _apply_order(
        groups[RequestLocation.COOKIE],
        wire.cookie_order if wire else None,
        "cookie",
        compiled.wire_names,
    )
    body_slots = groups[RequestLocation.BODY]
    projection = compiled.body_projection
    flat_body_slots = tuple(
        slot
        for slot in body_slots
        if isinstance(compiled.descriptors[slot], JsonField | Form | Part)
    )
    if projection is not None:
        body_layout = BodyLayout(
            projection.encoding,
            wire.body_order if wire else None,
            projected=True,
        )
    elif flat_body_slots:
        body_slots = _apply_order(
            flat_body_slots,
            wire.body_order if wire else None,
            "body",
            compiled.wire_names,
        )
        marker = compiled.descriptors[body_slots[0]]
        if isinstance(marker, JsonField):
            descriptor: object | None = JsonBody()
        elif isinstance(marker, Form):
            descriptor = FormBody()
        else:
            descriptor = MultipartBody()
        body_layout = BodyLayout(descriptor, slots=body_slots, flat=True)
    else:
        descriptor = compiled.descriptors[body_slots[0]] if body_slots else None
        body_layout = BodyLayout(
            descriptor,
            wire.body_order if wire else None,
            slots=body_slots,
        )
    return RequestLayout(
        path=groups[RequestLocation.PATH],
        query=query,
        headers=headers,
        cookies=cookies,
        body=body_layout,
    )


def _apply_order(
    slots: tuple[ValueSlot[Any], ...],
    order: tuple[str, ...] | None,
    location: str,
    wire_names: Mapping[ValueSlot[object], str],
) -> tuple[ValueSlot[Any], ...]:
    if order is None:
        return slots
    by_name = {wire_names[slot]: slot for slot in slots}
    if len(order) != len(set(name.lower() for name in order)):
        raise PlanError(f"duplicate {location} order entry")
    unknown = set(order) - by_name.keys()
    if unknown:
        raise PlanError(f"unknown {location} order entries: {sorted(unknown)}")
    omitted_required = {
        name for name, slot in by_name.items() if slot.required and name not in set(order)
    }
    if omitted_required:
        raise PlanError(f"required {location} slots omitted from order: {sorted(omitted_required)}")
    ordered = [by_name[name] for name in order]
    ordered.extend(slot for slot in slots if slot not in ordered)
    return tuple(ordered)


def _query_parts(
    slots: tuple[ValueSlot[Any], ...],
    values: OperationValues,
    descriptors: Mapping[ValueSlot[object], object],
    wire_names: Mapping[ValueSlot[object], str],
    *,
    operation_id: str,
    models: ModelAdapterRegistry,
) -> tuple[tuple[PreparedQueryPair, ...], bytes]:
    output: list[PreparedQueryPair] = []
    raw_query: bytes | None = None
    for slot in slots:
        if not values.contains(slot):
            continue
        value = values.require(slot)
        descriptor = descriptors[slot]
        if isinstance(descriptor, Query):
            value = _scalar_value(
                descriptor.codec,
                value,
                location="query",
                wire_name=descriptor.name or slot.diagnostic_name,
                operation_id=operation_id,
            )
            serialized = serialize_query(descriptor, value)
        elif isinstance(descriptor, QueryString):
            serialized = serialize_querystring(descriptor, value, models=models)
        else:
            serialized = serialize_query(Query(wire_names[slot]), value)
        if serialized.raw_query is not None:
            if output or raw_query is not None:
                raise PlanError(
                    "a querystring parameter cannot be combined with other query values"
                )
            try:
                raw_query = serialized.raw_query.encode("ascii")
            except UnicodeEncodeError as exc:
                raise PlanError("raw querystring must contain ASCII wire characters") from exc
            continue
        if raw_query is not None:
            raise PlanError("a querystring parameter cannot be combined with other query values")
        for name, raw, encoded_name, encoded_value in serialized.encoded_items():
            output.append(
                PreparedQueryPair(
                    name=name.encode("utf-8"),
                    value=raw.encode("utf-8"),
                    encoded_name=encoded_name.encode("ascii"),
                    encoded_value=encoded_value.encode("ascii"),
                    slot=slot,
                )
            )
    rendered = raw_query if raw_query is not None else b"&".join(pair.wire for pair in output)
    return tuple(output), rendered


def _existing_query(value: str) -> tuple[tuple[PreparedQueryPair, ...], bytes]:
    if not value:
        return (), b""
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PlanError("existing URL query must contain ASCII wire characters") from exc
    output: list[PreparedQueryPair] = []
    for field in encoded.split(b"&"):
        name, separator, item = field.partition(b"=")
        encoded_value = item if separator else b""
        output.append(
            PreparedQueryPair(
                name=unquote_to_bytes(name),
                value=unquote_to_bytes(encoded_value),
                encoded_name=name,
                encoded_value=encoded_value,
            )
        )
    result = tuple(output)
    _ensure_unique_query_pairs(result)
    return result, encoded


def _ensure_unique_query_pairs(pairs: Sequence[PreparedQueryPair]) -> None:
    seen: set[bytes] = set()
    for pair in pairs:
        if pair.name in seen:
            raise PlanError(
                f"duplicate query wire name {pair.name.decode('utf-8', errors='replace')!r}"
            )
        seen.add(pair.name)


def _headers(
    slots: tuple[ValueSlot[Any], ...],
    values: OperationValues,
    descriptors: Mapping[ValueSlot[object], object],
    wire_names: Mapping[ValueSlot[object], str],
    *,
    operation_id: str,
) -> tuple[HeaderField, ...]:
    output: list[HeaderField] = []
    seen_single: set[bytes] = set()
    for slot in slots:
        if not values.contains(slot):
            continue
        name = _header_name(wire_names[slot])
        lowered = name.lower()
        value = values.require(slot)
        descriptor = descriptors[slot]
        if isinstance(descriptor, Header) and descriptor.codec is not None:
            value = _scalar_value(
                descriptor.codec,
                value,
                location="header",
                wire_name=descriptor.name or slot.diagnostic_name,
                operation_id=operation_id,
            )
            serialized = serialize_header(descriptor, value)
            items = () if serialized is None else (serialized,)
        elif isinstance(descriptor, Header) and not _is_sequence(value):
            serialized = serialize_header(descriptor, value)
            items = () if serialized is None else (serialized,)
        else:
            # A sequence intentionally represents duplicate field lines in the prepared model.
            items = value if _is_sequence(value) else (value,)
        if lowered in seen_single:
            raise PlanError(f"case-colliding header declaration: {name.decode()}")
        seen_single.add(lowered)
        output.extend(
            HeaderField(name, _header_value(name, item), slot, slot.secret) for item in items
        )
    return tuple(output)


def _cookies(
    slots: tuple[ValueSlot[Any], ...],
    values: OperationValues,
    descriptors: Mapping[ValueSlot[object], object],
    wire_names: Mapping[ValueSlot[object], str],
    *,
    operation_id: str,
) -> tuple[PreparedCookie, ...]:
    from eazy_sdk.core.http import ManagedCookieSetDescriptor

    output: list[PreparedCookie] = []
    seen: set[bytes] = set()
    for slot in slots:
        if values.contains(slot):
            descriptor = descriptors[slot]
            value = values.require(slot)
            if isinstance(descriptor, ManagedCookieSetDescriptor):
                if not isinstance(value, tuple):
                    raise BindingError("managed cookie set must be a validated tuple")
                for item in value:
                    if (
                        not isinstance(item, tuple)
                        or len(item) != 2
                        or not isinstance(item[0], str)
                        or not isinstance(item[1], str)
                    ):
                        raise BindingError("managed cookie set contains an invalid item")
                    cookie_name = _header_name(item[0])
                    if cookie_name in seen:
                        raise PlanError(f"duplicate cookie wire name {item[0]!r}")
                    seen.add(cookie_name)
                    output.append(
                        PreparedCookie(cookie_name, _header_value(b"Cookie", item[1]), slot)
                    )
                continue
            if isinstance(descriptor, Cookie):
                value = _scalar_value(
                    descriptor.codec,
                    value,
                    location="cookie",
                    wire_name=descriptor.name or slot.diagnostic_name,
                    operation_id=operation_id,
                )
            serialized = (
                serialize_cookie(descriptor, value)
                if isinstance(descriptor, Cookie)
                else (wire_names[slot], _primitive(value))
            )
            if serialized is not None:
                serialized_name, rendered = serialized
                encoded_name = _header_name(serialized_name)
                if encoded_name in seen:
                    raise PlanError(f"duplicate cookie wire name {serialized_name!r}")
                seen.add(encoded_name)
                output.append(
                    PreparedCookie(encoded_name, _header_value(b"Cookie", rendered), slot)
                )
    return tuple(output)


_HEADER_NAME_BYTES = frozenset(
    b"!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


def _header_name(value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PlanError(f"invalid request header name: {value!r}") from exc
    if not encoded or any(item not in _HEADER_NAME_BYTES for item in encoded):
        raise PlanError(f"invalid request header name: {value!r}")
    return encoded


def _header_value(name: bytes, value: object) -> bytes:
    encoded = _primitive(value).encode("utf-8")
    if any(item in encoded for item in (b"\r", b"\n", b"\x00")):
        raise PlanError(f"invalid request header value for {name.decode('ascii')!r}")
    return encoded


def _body(
    layout: BodyLayout,
    values: OperationValues,
    *,
    boundary: str | None,
    models: ModelAdapterRegistry,
    descriptors: Mapping[ValueSlot[object], object],
    wire_names: Mapping[ValueSlot[object], str],
    operation_id: str,
    document_override: object,
) -> tuple[BufferedBody | ReplayableBodyStream, PreparedBodyView, ZaprosBodyInput]:
    descriptor = layout.descriptor
    body_slots = layout.slots
    body_sensitive = any(slot.secret for slot in body_slots)
    present_slots = tuple(slot for slot in body_slots if values.contains(slot))
    has_document_override = document_override is not _NO_BODY_DOCUMENT_OVERRIDE
    if descriptor is None or (
        not layout.projected and not present_slots and not has_document_override
    ):
        empty = BufferedBody(b"", None)
        return empty, PreparedBodyView(b"", None, sensitive=body_sensitive), NoBodyInput()
    value: object
    if layout.projected:
        if not has_document_override:
            raise PlanError("body projection document is missing from the attempt")
        value = document_override
    elif layout.flat:
        flat_values: dict[str, object] = {}
        for slot in body_slots:
            if not values.contains(slot):
                continue
            item = values.require(slot)
            field_descriptor = descriptors[slot]
            if isinstance(field_descriptor, Form):
                item = _scalar_value(
                    field_descriptor.codec,
                    item,
                    location="form",
                    wire_name=field_descriptor.name or slot.diagnostic_name,
                    operation_id=operation_id,
                )
            flat_values[wire_names[slot]] = item
        value = flat_values
    else:
        value = values.require(body_slots[0])
    if isinstance(descriptor, JsonBody):
        semantic = (
            _to_json_value(value, models=models)
            if not has_document_override
            else document_override
        )
        semantic = _order_mapping(semantic, layout.field_order)
        content = json.dumps(
            semantic,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
        frozen = _freeze_json(semantic)
        return (
            BufferedBody(content, descriptor.content_type.encode("ascii")),
            PreparedBodyView(
                content, descriptor.content_type, frozen, sensitive=body_sensitive
            ),
            JsonInput(semantic),
        )
    if has_document_override and not layout.projected:
        raise PlanError("document body override requires a JSON body descriptor")
    if isinstance(descriptor, FormBody):
        mapping = (
            _primitive_mapping(value, kind="form")
            if layout.projected
            else _as_mapping(value, models=models)
        )
        mapping = cast(Mapping[str, object], _order_mapping(mapping, layout.field_order))
        fields: list[PreparedFormField] = []
        for name, item in mapping.items():
            if _is_sequence(item):
                raise BindingError(
                    f"form field {name!r} requires an explicit single-value scalar codec"
                )
            fields.append(PreparedFormField(name.encode("utf-8"), _primitive(item).encode("utf-8")))
        content = b"&".join(
            _quote_bytes(field.name, plus=True) + b"=" + _quote_bytes(field.value, plus=True)
            for field in fields
        )
        return (
            BufferedBody(content, descriptor.content_type.encode("ascii")),
            PreparedBodyView(
                content,
                descriptor.content_type,
                form_fields=tuple(fields),
                sensitive=body_sensitive,
            ),
            FormInput(
                tuple((field.name.decode("utf-8"), field.value.decode("utf-8")) for field in fields)
            ),
        )
    if isinstance(descriptor, MultipartBody):
        actual_boundary = boundary or descriptor.boundary or "eazy_sdk-boundary"
        mapping = cast(
            Mapping[str, object],
            _order_mapping(
                (
                    _primitive_mapping(value, kind="multipart")
                    if layout.projected
                    else _multipart_mapping(value, models=models)
                ),
                layout.field_order,
            ),
        )
        content = _multipart(mapping, actual_boundary)
        media_type = f"{descriptor.content_type}; boundary={actual_boundary}"
        return (
            BufferedBody(content, media_type.encode("ascii")),
            PreparedBodyView(content, media_type, sensitive=body_sensitive),
            _multipart_input(mapping, actual_boundary),
        )
    if isinstance(descriptor, BytesBody):
        if not isinstance(value, bytes | bytearray | memoryview):
            raise BindingError("bytes body requires a bytes-like value")
        content = bytes(value)
        if descriptor.content_encoding == "gzip":
            content = gzip.compress(content, mtime=0)
        elif descriptor.content_encoding == "deflate":
            content = zlib.compress(content)
        return (
            BufferedBody(
                content,
                descriptor.content_type.encode("ascii") if descriptor.content_type else None,
            ),
            PreparedBodyView(content, descriptor.content_type, sensitive=body_sensitive),
            ExactBodyInput(content, descriptor.content_type),
        )
    if isinstance(descriptor, ReplayableStreamBody):
        if not isinstance(value, ReplayableBodyStream):
            raise BindingError("stream body requires ReplayableBodyStream with a fresh factory")
        probe = value.factory()
        if not callable(getattr(probe, "read", None)):
            raise BindingError("stream factory must return a binary readable object")
        probe.close()
        return (
            value,
            PreparedBodyView(b"", descriptor.content_type, sensitive=body_sensitive),
            StreamBodyInput(value.factory, value.known_length, descriptor.content_type),
        )
    if isinstance(descriptor, BodyCodec):
        semantic = value if layout.projected else models.dump(value)
        content = descriptor.encode(semantic, EncodeContext(operation_id))
        if not isinstance(content, bytes):
            raise BindingError("custom body codec must return bytes")
        custom_media_type: object = descriptor.media_type
        if custom_media_type is not None and not isinstance(custom_media_type, str):
            raise PlanError("custom body content_type must be a string or None")
        return (
            BufferedBody(
                content,
                custom_media_type.encode("ascii") if custom_media_type else None,
            ),
            PreparedBodyView(content, custom_media_type, sensitive=body_sensitive),
            ExactBodyInput(content, custom_media_type),
        )
    raise PlanError(f"unsupported body descriptor: {type(descriptor).__name__}")


def _automatic_headers(
    headers: tuple[HeaderField, ...],
    *,
    authority: bytes,
    body: BufferedBody | ReplayableBodyStream,
    content_type: bytes | None,
    cookies: tuple[PreparedCookie, ...],
) -> tuple[HeaderField, ...]:
    output = list(headers)
    names = {field.name.lower() for field in output}
    automatic: list[HeaderField] = []
    if b"host" not in names:
        automatic.append(HeaderField(b"Host", authority))
    if content_type is not None and b"content-type" not in names:
        automatic.append(HeaderField(b"Content-Type", content_type))
    length = len(body.content) if isinstance(body, BufferedBody) else body.known_length
    if length is not None and b"content-length" not in names:
        automatic.append(HeaderField(b"Content-Length", str(length).encode("ascii")))
    if cookies and b"cookie" not in names:
        automatic.append(
            HeaderField(
                b"Cookie",
                b"; ".join(cookie.name + b"=" + cookie.value for cookie in cookies),
                sensitive=True,
            )
        )
    return tuple((*output, *automatic))


def _body_content_type(descriptor: object | None) -> bytes | None:
    value = (
        descriptor.media_type
        if isinstance(descriptor, BodyCodec)
        else getattr(descriptor, "content_type", None)
    )
    return value.encode("ascii") if isinstance(value, str) else None


def _multipart(mapping: Mapping[str, object], boundary: str) -> bytes:
    marker = boundary.encode("ascii")
    output = BytesIO()
    for name, raw in mapping.items():
        values = (
            cast(Sequence[object], raw)
            if _is_sequence(raw) and not isinstance(raw, MultipartPart)
            else (raw,)
        )
        for value in values:
            part = (
                value
                if isinstance(value, MultipartPart)
                else MultipartPart(
                    bytes(value)
                    if isinstance(value, bytes | bytearray | memoryview)
                    else _primitive(value).encode("utf-8")
                )
            )
            output.write(b"--" + marker + b"\r\n")
            disposition = f'form-data; name="{name}"'
            if part.filename is not None:
                disposition += f'; filename="{part.filename}"'
            output.write(b"Content-Disposition: " + disposition.encode("utf-8") + b"\r\n")
            if part.content_type is not None:
                output.write(b"Content-Type: " + part.content_type.encode("ascii") + b"\r\n")
            for header, header_value in part.headers:
                output.write(
                    header.encode("ascii") + b": " + header_value.encode("utf-8") + b"\r\n"
                )
            output.write(b"\r\n" + part.content + b"\r\n")
    output.write(b"--" + marker + b"--\r\n")
    return output.getvalue()


def _multipart_input(mapping: Mapping[str, object], boundary: str) -> MultipartInput:
    output: list[MultipartInputPart] = []
    for name, raw in mapping.items():
        values = (
            cast(Sequence[object], raw)
            if _is_sequence(raw) and not isinstance(raw, MultipartPart)
            else (raw,)
        )
        for value in values:
            part = (
                value
                if isinstance(value, MultipartPart)
                else MultipartPart(
                    bytes(value)
                    if isinstance(value, bytes | bytearray | memoryview)
                    else _primitive(value).encode("utf-8")
                )
            )
            output.append(
                MultipartInputPart(
                    name,
                    part.content,
                    part.filename,
                    part.content_type,
                    part.headers,
                )
            )
    return MultipartInput(tuple(output), boundary)


def _to_json_value(
    value: object,
    *,
    models: ModelAdapterRegistry,
) -> object:
    return models.dump(value)


def _order_mapping(value: object, order: tuple[str, ...] | None) -> object:
    if order is None:
        return value
    if not isinstance(value, Mapping):
        raise BindingError("explicit body order requires an object-shaped body")
    if len(order) != len(set(order)):
        raise PlanError("duplicate body order entry")
    unknown = set(order) - value.keys()
    if unknown:
        raise PlanError(f"unknown body order entries: {sorted(unknown)}")
    return {
        **{name: value[name] for name in order},
        **{key: item for key, item in value.items() if key not in order},
    }


def _as_mapping(value: object, *, models: ModelAdapterRegistry) -> Mapping[str, object]:
    converted = _to_json_value(value, models=models)
    if not isinstance(converted, Mapping):
        raise BindingError("form or multipart body must be object-shaped")
    return cast(Mapping[str, object], converted)


def _primitive_mapping(value: object, *, kind: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BindingError(f"{kind} body projection must produce an object-shaped document")
    return cast(Mapping[str, object], value)


def _multipart_mapping(value: object, *, models: ModelAdapterRegistry) -> Mapping[str, object]:
    converted = value if isinstance(value, Mapping) else models.dump_model(value, mode="python")
    if isinstance(converted, Mapping):
        return cast(Mapping[str, object], converted)
    raise BindingError("multipart body must be object-shaped")


def _freeze_json(value: object) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        return tuple((str(key), _freeze_json(item)) for key, item in value.items())
    if _is_sequence(value):
        return tuple(_freeze_json(item) for item in cast(Sequence[object], value))
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def _primitive(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return _primitive(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _scalar_value(
    codec: ScalarCodec | None,
    value: object,
    *,
    location: Literal["path", "query", "header", "cookie", "form"],
    wire_name: str,
    operation_id: str,
) -> object:
    if codec is None:
        return value
    encoded = codec.encode(value, ScalarEncodeContext(location, wire_name, operation_id))
    if not isinstance(encoded, str):
        raise BindingError(f"scalar codec {codec.name!r} must return str")
    return encoded


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _quote_bytes(value: bytes, *, plus: bool = False) -> bytes:
    encoded = quote(value.decode("utf-8"), safe="-._~").encode("ascii")
    return encoded.replace(b"%20", b"+") if plus else encoded


def _resolve_url(base_url: str, path: str) -> str:
    split = urlsplit(path)
    if split.scheme:
        return path
    if not base_url:
        raise PlanError("relative endpoint path requires a base URL")
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
