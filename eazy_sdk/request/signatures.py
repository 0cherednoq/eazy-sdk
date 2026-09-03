"""Declarative and named procedural signing over immutable prepared requests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Literal, Protocol, cast

from eazy_sdk.core.errors import GraphError, PlanError, WriterConflictError
from eazy_sdk.core.http import RequestLocation
from eazy_sdk.core.http_plan import RequestScope, ScopeContext
from eazy_sdk.core.kernel import PythonTypeValidator, ValueSlot
from eazy_sdk.request.prepared import (
    BufferedBody,
    HeaderField,
    PreparedBodyView,
    PreparedCookie,
    PreparedFormField,
    PreparedQueryPair,
    PreparedRequest,
    PreparedRequestView,
    ReservedOutput,
    UnsignedPreparedRequest,
)


@dataclass(frozen=True, slots=True, eq=False)
class SignatureIdentity:
    name: str


@dataclass(frozen=True, slots=True, eq=False)
class SigningKeyRequirement:
    name: str


class SigningKey:
    __slots__ = ("_value",)

    def __init__(self, value: bytes | str) -> None:
        self._value = value.encode() if isinstance(value, str) else bytes(value)

    def reveal(self) -> bytes:
        return self._value

    def __repr__(self) -> str:
        return "SigningKey(<redacted>)"


@dataclass(frozen=True, slots=True)
class SigningInput:
    request: PreparedRequestView
    components: Mapping[SignatureIdentity, bytes]
    stage: int

    def __repr__(self) -> str:
        return (
            f"SigningInput(method={self.request.method!r}, target_length="
            f"{len(self.request.target)}, stage={self.stage})"
        )


class SignatureAlgorithm(Protocol):
    def sign(self, base: bytes, key: SigningKey) -> bytes: ...


@dataclass(frozen=True, slots=True)
class HmacSha256:
    def sign(self, base: bytes, key: SigningKey) -> bytes:
        return hmac.new(key.reveal(), base, hashlib.sha256).digest()


class SignatureComponent(Protocol):
    @property
    def reads(self) -> frozenset[str]: ...

    def build(self, signing: SigningInput) -> bytes: ...


@dataclass(frozen=True, slots=True)
class RequestComponent:
    kind: Literal["method", "scheme", "authority", "target", "path", "body"]

    @property
    def reads(self) -> frozenset[str]:
        return frozenset({self.kind})

    def build(self, signing: SigningInput) -> bytes:
        if self.kind == "body":
            return signing.request.body.content
        return cast(bytes, getattr(signing.request, self.kind))


@dataclass(frozen=True, slots=True)
class HeaderComponent:
    name: str
    occurrence: int | Literal["all"] = "all"
    separator: bytes = b","

    @property
    def reads(self) -> frozenset[str]:
        return frozenset({f"header:{self.name.lower()}"})

    def build(self, signing: SigningInput) -> bytes:
        values = [
            item.value
            for item in signing.request.headers
            if item.name.decode("ascii").lower() == self.name.lower()
        ]
        if self.occurrence == "all":
            return self.separator.join(values)
        try:
            return values[self.occurrence]
        except IndexError as exc:
            raise PlanError(f"missing header occurrence: {self.name}[{self.occurrence}]") from exc


@dataclass(frozen=True, slots=True)
class BodyDigestComponent:
    algorithm: str = "sha256"
    encoding: Literal["hex", "base64", "raw"] = "hex"

    @property
    def reads(self) -> frozenset[str]:
        return frozenset({"body"})

    def build(self, signing: SigningInput) -> bytes:
        try:
            digest = hashlib.new(self.algorithm, signing.request.body.content).digest()
        except ValueError as exc:
            raise PlanError(f"unsupported digest algorithm: {self.algorithm}") from exc
        return _encode(digest, self.encoding)


@dataclass(frozen=True, slots=True)
class QueryProjection:
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    order: Literal["wire", "canonical"] = "wire"
    separator: bytes = b"&"

    @property
    def reads(self) -> frozenset[str]:
        return frozenset({"query"})

    def build(self, signing: SigningInput) -> bytes:
        pairs = [
            pair
            for pair in signing.request.query_pairs
            if (self.include is None or pair.name.decode() in self.include)
            and pair.name.decode() not in self.exclude
        ]
        if self.order == "canonical":
            pairs.sort(key=lambda pair: (pair.encoded_name, pair.encoded_value))
        return self.separator.join(pair.wire for pair in pairs)


@dataclass(frozen=True, slots=True)
class JsonProjection:
    include: tuple[str, ...] | None = None
    sort_keys: bool = True
    omit_null: bool = False

    @property
    def reads(self) -> frozenset[str]:
        return frozenset({"json"})

    def build(self, signing: SigningInput) -> bytes:
        semantic = _thaw(signing.request.body.json_view)
        if semantic is None:
            raise PlanError("JSON projection requires a prepared JSON body")
        selected: object = semantic
        if self.include is not None:
            selected = {pointer: _json_pointer(semantic, pointer) for pointer in self.include}
        if self.omit_null and isinstance(selected, Mapping):
            selected = {key: value for key, value in selected.items() if value is not None}
        return json.dumps(
            selected,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=self.sort_keys,
        ).encode()


@dataclass(frozen=True, slots=True)
class PreviousSignature:
    identity: SignatureIdentity

    @property
    def reads(self) -> frozenset[str]:
        return frozenset({f"signature:{id(self.identity)}"})

    def build(self, signing: SigningInput) -> bytes:
        try:
            return signing.components[self.identity]
        except KeyError as exc:
            raise PlanError(f"signature {self.identity.name!r} is not available") from exc


@dataclass(frozen=True, slots=True)
class LiteralComponent:
    value: bytes

    @property
    def reads(self) -> frozenset[str]:
        return frozenset()

    def build(self, signing: SigningInput) -> bytes:
        return self.value


@dataclass(frozen=True, slots=True)
class Join:
    parts: tuple[SignatureComponent, ...]
    separator: bytes
    prefix: bytes = b""
    suffix: bytes = b""

    @property
    def reads(self) -> frozenset[str]:
        return frozenset().union(*(part.reads for part in self.parts))

    def build(self, signing: SigningInput) -> bytes:
        return (
            self.prefix
            + self.separator.join(part.build(signing) for part in self.parts)
            + self.suffix
        )


@dataclass(frozen=True, slots=True)
class CustomBase:
    implementation: CustomCanonicalizer
    read_set: frozenset[str]

    @property
    def reads(self) -> frozenset[str]:
        return self.read_set

    def build(self, signing: SigningInput) -> bytes:
        return self.implementation.build(signing)


class CustomCanonicalizer(Protocol):
    def build(self, signing: SigningInput) -> bytes: ...


class SignatureOutputEncoding(Enum):
    HEX = "hex"
    BASE64 = "base64"
    RAW = "raw"


@dataclass(frozen=True, slots=True, eq=False)
class SignatureOutput:
    name: str
    location: RequestLocation
    encoding: SignatureOutputEncoding = SignatureOutputEncoding.HEX
    position: int | None = None
    json_pointer: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("signature output name must not be empty")
        if self.json_pointer is not None:
            if self.location is not RequestLocation.BODY:
                raise ValueError("json_pointer is supported only for body signature outputs")
            _body_output_path(self)

    @property
    def key(self) -> tuple[RequestLocation, str]:
        if self.location is RequestLocation.BODY:
            return self.location, "/".join(_body_output_path(self))
        return self.location, self.name.lower()


@dataclass(frozen=True, slots=True)
class DeclarativeSignature:
    identity: SignatureIdentity
    algorithm: SignatureAlgorithm
    base: SignatureComponent
    outputs: tuple[SignatureOutput, ...]
    key: SigningKeyRequirement


class CustomRequestSigner(Protocol):
    def sign(self, signing: SigningInput, key: SigningKey | None) -> SignatureResult: ...


@dataclass(frozen=True, slots=True)
class SignatureResult:
    outputs: Mapping[SignatureOutput, bytes | str]


@dataclass(frozen=True, slots=True)
class CustomSignature:
    identity: SignatureIdentity
    signer: CustomRequestSigner
    read_set: frozenset[str]
    outputs: tuple[SignatureOutput, ...]
    key: SigningKeyRequirement | None = None


type RequestSignature = DeclarativeSignature | CustomSignature
type SigningKeyProvider = Callable[[SigningKeyRequirement], SigningKey]


@dataclass(frozen=True, slots=True)
class SignaturePlan:
    signatures: tuple[RequestSignature, ...]
    reserved_outputs: tuple[ReservedOutput, ...]


class SigningOverrideMode(Enum):
    USE = "use"
    EXTEND = "extend"
    UNSIGNED = "unsigned"


@dataclass(frozen=True, slots=True)
class SigningOverride:
    mode: SigningOverrideMode
    signatures: tuple[RequestSignature, ...] = ()


@dataclass(frozen=True, slots=True)
class SigningRule:
    signatures: tuple[RequestSignature, ...]
    scope: RequestScope


def compile_signatures(signatures: Sequence[RequestSignature]) -> SignaturePlan:
    writers: dict[tuple[RequestLocation, str], RequestSignature] = {}
    identities = {id(signature.identity): signature for signature in signatures}
    edges: dict[int, set[int]] = defaultdict(set)
    indegree = {id(signature): 0 for signature in signatures}
    slots: list[ReservedOutput] = []
    for signature in signatures:
        reads = (
            signature.base.reads
            if isinstance(signature, DeclarativeSignature)
            else signature.read_set
        )
        for output in signature.outputs:
            previous = writers.get(output.key)
            if previous is not None:
                raise WriterConflictError(
                    f"signature output {output.name!r} has writers "
                    f"{previous.identity.name!r} and {signature.identity.name!r}"
                )
            writers[output.key] = signature
            if isinstance(signature, DeclarativeSignature) and _output_conflicts_with_reads(
                output, reads
            ):
                raise GraphError(
                    f"self-signing cycle: {signature.identity.name} -> {output.name} -> "
                    f"{signature.identity.name}"
                )
            slot: ValueSlot[bytes | str] = ValueSlot(
                f"signature.{signature.identity.name}.{output.name}",
                PythonTypeValidator(bytes | str),
                required=True,
                secret=True,
            )
            slots.append(ReservedOutput(output, output.location, slot))
        for read in reads:
            if read.startswith("signature:"):
                identity = int(read.removeprefix("signature:"))
                source = identities.get(identity)
                if source is None:
                    raise GraphError(
                        f"signature {signature.identity.name!r} reads an unknown previous signature"
                    )
                edges[id(source)].add(id(signature))
                indegree[id(signature)] += 1
    by_id = {id(signature): signature for signature in signatures}
    ready = sorted(
        (signature for signature in signatures if indegree[id(signature)] == 0),
        key=lambda signature: signature.identity.name,
    )
    ordered: list[RequestSignature] = []
    while ready:
        signature = ready.pop(0)
        ordered.append(signature)
        for target in edges[id(signature)]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(by_id[target])
    if len(ordered) != len(signatures):
        names = [signature.identity.name for signature in signatures if indegree[id(signature)]]
        raise GraphError("signature graph cycle: " + " -> ".join((*names, names[0])))
    return SignaturePlan(tuple(ordered), tuple(slots))


def sign_prepared(
    unsigned: UnsignedPreparedRequest,
    plan: SignaturePlan,
    key_provider: SigningKeyProvider,
) -> PreparedRequest:
    request = unsigned
    completed: dict[SignatureIdentity, bytes] = {}
    reserved = {id(item.identity): item for item in unsigned.reserved_outputs}
    expected = {id(item.identity) for item in plan.reserved_outputs}
    if set(reserved) != expected:
        raise PlanError("unsigned request reserved outputs do not match signature plan")
    for stage, signature in enumerate(plan.signatures):
        view = request.view
        if view is None:
            raise PlanError("signing requires PreparedRequestView")
        signing = SigningInput(view, completed, stage)
        if isinstance(signature, DeclarativeSignature):
            base = signature.base.build(signing)
            raw = signature.algorithm.sign(base, key_provider(signature.key))
            values = {output: _encode(raw, output.encoding.value) for output in signature.outputs}
            completed[signature.identity] = raw
        else:
            key = key_provider(signature.key) if signature.key is not None else None
            result = signature.signer.sign(signing, key)
            undeclared = set(result.outputs) - set(signature.outputs)
            missing = set(signature.outputs) - set(result.outputs)
            if undeclared or missing:
                raise PlanError(
                    f"custom signature {signature.identity.name!r} output mismatch: "
                    f"undeclared={len(undeclared)}, missing={len(missing)}"
                )
            values = {
                output: value.encode() if isinstance(value, str) else value
                for output, value in result.outputs.items()
            }
            completed[signature.identity] = b"".join(values.values())
        for output, value in values.items():
            request = _apply_output(request, output, value)
            reserved.pop(id(output), None)
    if reserved:
        raise PlanError("not all reserved signature outputs were filled")
    return replace(request, reserved_outputs=()).finalize()


def reserve_outputs(plan: SignaturePlan) -> tuple[ReservedOutput, ...]:
    return plan.reserved_outputs


def hmac_sha256(
    *,
    key: SigningKeyRequirement,
    base: SignatureComponent,
    output: SignatureOutput | None = None,
    outputs: tuple[SignatureOutput, ...] = (),
    name: str = "hmac-sha256",
) -> DeclarativeSignature:
    selected = (*outputs, *((output,) if output is not None else ()))
    if not selected:
        raise ValueError("signature requires at least one output")
    return DeclarativeSignature(SignatureIdentity(name), HmacSha256(), base, selected, key)


def custom_signature(
    *,
    signer: CustomRequestSigner,
    reads: frozenset[str],
    outputs: tuple[SignatureOutput, ...],
    key: SigningKeyRequirement | None = None,
    name: str = "custom",
) -> CustomSignature:
    return CustomSignature(SignatureIdentity(name), signer, reads, outputs, key)


def use(*signatures: RequestSignature) -> SigningOverride:
    return SigningOverride(SigningOverrideMode.USE, signatures)


def extend(*signatures: RequestSignature) -> SigningOverride:
    return SigningOverride(SigningOverrideMode.EXTEND, signatures)


def unsigned() -> SigningOverride:
    return SigningOverride(SigningOverrideMode.UNSIGNED)


def sign(*signatures: RequestSignature, scope: RequestScope) -> SigningRule:
    return SigningRule(signatures, scope)


def select_signatures(
    *,
    context: ScopeContext,
    endpoint: SigningOverride | None = None,
    group_default: tuple[RequestSignature, ...] | None = None,
    api_default: tuple[RequestSignature, ...] | None = None,
    rules: tuple[SigningRule, ...] = (),
) -> tuple[RequestSignature, ...]:
    inherited = group_default if group_default is not None else api_default
    if inherited is None:
        inherited = tuple(
            signature
            for rule in rules
            if rule.scope.matches(context)
            for signature in rule.signatures
        )
    if endpoint is None:
        return inherited
    if endpoint.mode is SigningOverrideMode.UNSIGNED:
        return ()
    if endpoint.mode is SigningOverrideMode.USE:
        return endpoint.signatures
    return (*inherited, *endpoint.signatures)


def method() -> RequestComponent:
    return RequestComponent("method")


def scheme() -> RequestComponent:
    return RequestComponent("scheme")


def authority() -> RequestComponent:
    return RequestComponent("authority")


def target() -> RequestComponent:
    return RequestComponent("target")


def path() -> RequestComponent:
    return RequestComponent("path")


def wire_body() -> RequestComponent:
    return RequestComponent("body")


def header(name: str, *, occurrence: int | Literal["all"] = "all") -> HeaderComponent:
    return HeaderComponent(name, occurrence)


def body_digest(
    algorithm: str = "sha256", *, encoding: Literal["hex", "base64", "raw"] = "hex"
) -> BodyDigestComponent:
    return BodyDigestComponent(algorithm, encoding)


def query(
    *,
    include: tuple[str, ...] | None = None,
    exclude: tuple[str, ...] = (),
    order: Literal["wire", "canonical"] = "wire",
) -> QueryProjection:
    return QueryProjection(include, exclude, order)


def canonical_json(
    *, include: tuple[str, ...] | None = None, sort_keys: bool = True, omit_null: bool = False
) -> JsonProjection:
    return JsonProjection(include, sort_keys, omit_null)


def previous_signature(identity: SignatureIdentity) -> PreviousSignature:
    return PreviousSignature(identity)


def literal(value: bytes) -> LiteralComponent:
    return LiteralComponent(value)


def join(
    *parts: SignatureComponent,
    separator: bytes,
    prefix: bytes = b"",
    suffix: bytes = b"",
) -> Join:
    return Join(tuple(parts), separator, prefix, suffix)


def whole_prepared_request() -> frozenset[str]:
    return frozenset(
        {"method", "scheme", "authority", "target", "query", "headers", "body", "json"}
    )


def read_set(*components: SignatureComponent) -> frozenset[str]:
    return frozenset().union(*(component.reads for component in components))


def custom_base(
    implementation: CustomCanonicalizer, *, reads: frozenset[str] | None = None
) -> CustomBase:
    return CustomBase(implementation, reads or whole_prepared_request())


def header_output(
    name: str,
    *,
    encoding: Literal["hex", "base64", "raw"] = "hex",
    position: int | None = None,
) -> SignatureOutput:
    return SignatureOutput(
        name, RequestLocation.HEADER, SignatureOutputEncoding(encoding), position
    )


def query_output(
    name: str,
    *,
    encoding: Literal["hex", "base64", "raw"] = "hex",
    position: int | None = None,
) -> SignatureOutput:
    return SignatureOutput(name, RequestLocation.QUERY, SignatureOutputEncoding(encoding), position)


def cookie_output(
    name: str,
    *,
    encoding: Literal["hex", "base64", "raw"] = "hex",
    position: int | None = None,
) -> SignatureOutput:
    return SignatureOutput(
        name, RequestLocation.COOKIE, SignatureOutputEncoding(encoding), position
    )


def body_output(
    name: str,
    *,
    encoding: Literal["hex", "base64", "raw"] = "hex",
    position: int | None = None,
    json_pointer: str | None = None,
) -> SignatureOutput:
    return SignatureOutput(
        name,
        RequestLocation.BODY,
        SignatureOutputEncoding(encoding),
        position,
        json_pointer,
    )


def _output_conflicts_with_reads(output: SignatureOutput, reads: frozenset[str]) -> bool:
    if output.location is RequestLocation.QUERY:
        return "target" in reads or "query" in reads
    if output.location is RequestLocation.HEADER:
        return "headers" in reads or f"header:{output.name.lower()}" in reads
    if output.location is RequestLocation.COOKIE:
        return "headers" in reads or "header:cookie" in reads
    return "body" in reads or "json" in reads


def _apply_output(
    request: UnsignedPreparedRequest, output: SignatureOutput, value: bytes
) -> UnsignedPreparedRequest:
    view = request.view
    if view is None:
        raise PlanError("signature output requires prepared request view")
    if output.location is RequestLocation.HEADER:
        field = HeaderField(output.name.encode("ascii"), value, sensitive=True)
        headers = _insert(request.headers, field, output.position)
        return replace(request, headers=headers, view=replace(view, headers=headers))
    if output.location is RequestLocation.QUERY:
        raw_name = output.name.encode()
        pair = PreparedQueryPair(raw_name, value, _quote(raw_name), _quote(value))
        pairs = _insert(view.query_pairs, pair, output.position)
        query_bytes = b"&".join(item.wire for item in pairs)
        target_bytes = view.path + (b"?" + query_bytes if query_bytes else b"")
        return replace(
            request,
            target=target_bytes,
            view=replace(view, target=target_bytes, query_pairs=pairs),
        )
    if output.location is RequestLocation.COOKIE:
        cookie = PreparedCookie(output.name.encode(), value)
        cookies = _insert(view.cookies, cookie, output.position)
        cookie_value = b"; ".join(item.name + b"=" + item.value for item in cookies)
        headers = tuple(field for field in request.headers if field.name.lower() != b"cookie")
        headers = (*headers, HeaderField(b"Cookie", cookie_value, sensitive=True))
        return replace(
            request,
            headers=headers,
            view=replace(view, cookies=cookies, headers=headers),
        )
    body, body_view = _apply_body_output(request.body, view.body, output, value)
    headers = tuple(field for field in request.headers if field.name.lower() != b"content-length")
    headers = (*headers, HeaderField(b"Content-Length", str(len(body.content)).encode()))
    return replace(
        request,
        body=body,
        headers=headers,
        view=replace(view, body=body_view, headers=headers),
    )


def _apply_body_output(
    body: BufferedBody | object,
    view: PreparedBodyView,
    output: SignatureOutput,
    value: bytes,
) -> tuple[BufferedBody, PreparedBodyView]:
    if not isinstance(body, BufferedBody):
        raise PlanError("signature body output requires a buffered body")
    if view.json_view is not None:
        semantic = _thaw(view.json_view)
        if not isinstance(semantic, dict):
            raise PlanError("JSON signature output requires an object body")
        semantic = _insert_json_output(
            semantic,
            _body_output_path(output),
            value.decode(),
            output.position,
        )
        content = json.dumps(semantic, ensure_ascii=False, separators=(",", ":")).encode()
        frozen = cast(Any, _freeze(semantic))
        return BufferedBody(content, body.content_type), replace(
            view, content=content, json_view=frozen
        )
    if view.form_fields is not None:
        field = PreparedFormField(output.name.encode(), value)
        fields = _insert(view.form_fields, field, output.position)
        content = b"&".join(_quote(item.name) + b"=" + _quote(item.value) for item in fields)
        return BufferedBody(content, body.content_type), replace(
            view, content=content, form_fields=fields
        )
    raise PlanError("body signature output requires JSON or form semantic view")


def _body_output_path(output: SignatureOutput) -> tuple[str, ...]:
    pointer = output.json_pointer
    if pointer is None:
        return (output.name,)
    if not pointer.startswith("/") or pointer == "/":
        raise ValueError("body signature json_pointer must select a non-root object field")
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        index = 0
        while index < len(raw):
            if raw[index] == "~" and (index + 1 == len(raw) or raw[index + 1] not in "01"):
                raise ValueError("body signature json_pointer contains invalid escaping")
            index += 1
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    if any(not token for token in tokens):
        raise ValueError("body signature json_pointer components must not be empty")
    return tuple(tokens)


def _insert_json_output(
    document: dict[str, object],
    path: tuple[str, ...],
    value: str,
    position: int | None,
) -> dict[str, object]:
    root = dict(document)
    current = root
    for component in path[:-1]:
        nested = current.get(component)
        if nested is None:
            copied: dict[str, object] = {}
        elif isinstance(nested, Mapping):
            copied = dict(nested)
        else:
            raise PlanError(
                f"JSON signature output cannot traverse {'.'.join(path)!r}"
            )
        current[component] = copied
        current = copied
    terminal = path[-1]
    if terminal in current:
        raise WriterConflictError(
            f"JSON signature output target is already occupied: {'.'.join(path)}"
        )
    items = list(current.items())
    insertion = len(items) if position is None else position
    if insertion < 0 or insertion > len(items):
        raise PlanError(f"reserved output position is out of range: {insertion}")
    items.insert(insertion, (terminal, value))
    current.clear()
    current.update(items)
    return root


def _insert[T](values: tuple[T, ...], value: T, position: int | None) -> tuple[T, ...]:
    index = len(values) if position is None else position
    if index < 0 or index > len(values):
        raise PlanError(f"reserved output position is out of range: {index}")
    return (*values[:index], value, *values[index:])


def _encode(value: bytes, encoding: str) -> bytes:
    if encoding == "hex":
        return value.hex().encode()
    if encoding == "base64":
        return base64.b64encode(value)
    return value


def _quote(value: bytes) -> bytes:
    from urllib.parse import quote_from_bytes

    return quote_from_bytes(value, safe="-._~").encode()


def _thaw(value: object) -> object:
    if isinstance(value, tuple):
        if all(
            isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str)
            for item in value
        ):
            return {item[0]: _thaw(item[1]) for item in value}
        return [_thaw(item) for item in value]
    return value


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple((str(key), _freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _json_pointer(value: object, pointer: str) -> object:
    current = value
    if pointer == "":
        return current
    if not pointer.startswith("/"):
        raise PlanError(f"invalid JSON pointer: {pointer!r}")
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if key not in current:
                raise PlanError(f"JSON pointer is missing: {pointer!r}")
            current = current[key]
        elif isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError) as exc:
                raise PlanError(f"JSON pointer is missing: {pointer!r}") from exc
        else:
            raise PlanError(f"JSON pointer is missing: {pointer!r}")
    return current
