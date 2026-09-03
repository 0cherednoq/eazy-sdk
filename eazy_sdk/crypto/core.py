"""Application-owned payload crypto declarations.

Eazy SDK deliberately provides orchestration contracts only.  Applications own
the algorithms, keys, nonces and key lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, cast, get_args, get_origin

from eazy_sdk.core.errors import ConfigurationError, EazySdkError
from eazy_sdk.core.kernel import PythonTypeValidator, ValueValidator
from eazy_sdk.dependencies import RequestDependency
from eazy_sdk.models import ModelAdapterRegistry
from eazy_sdk.models.adapters import unwrap_annotated

type JsonPathComponent = str | int
type JsonPath = tuple[JsonPathComponent, ...]


@dataclass(frozen=True, slots=True)
class FrozenArray:
    items: tuple[FrozenValue, ...]


@dataclass(frozen=True, slots=True)
class FrozenObject:
    items: tuple[tuple[str, FrozenValue], ...]


type FrozenValue = None | bool | int | float | str | FrozenArray | FrozenObject


def freeze_value(value: object) -> FrozenValue:
    """Copy a JSON-compatible value into an immutable representation."""

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("crypto document object keys must be strings")
        return FrozenObject(tuple((key, freeze_value(item)) for key, item in sorted(value.items())))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return FrozenArray(tuple(freeze_value(item) for item in value))
    raise TypeError(f"unsupported crypto document value: {type(value).__name__}")


def thaw_value(value: FrozenValue) -> object:
    """Return a mutable JSON-compatible copy of an immutable document."""

    if isinstance(value, FrozenObject):
        return {key: thaw_value(item) for key, item in value.items}
    if isinstance(value, FrozenArray):
        return [thaw_value(item) for item in value.items]
    return value


class CryptoDirection(Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class CryptoStage(Enum):
    DOCUMENT = "document"
    ENCODED = "encoded"


class CryptoInputScope(Enum):
    OPERATION = "operation"
    CONNECTION = "connection"


@dataclass(frozen=True, slots=True, eq=False)
class CryptoInput[T]:
    name: str
    dependency: RequestDependency[T] = field(repr=False)
    scope: CryptoInputScope = CryptoInputScope.OPERATION
    aad: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise CryptoConfigurationError("crypto input name must not be empty")


@dataclass(frozen=True, slots=True, eq=False)
class CryptoOutput[T]:
    name: str
    validator: ValueValidator[T] = field(repr=False)
    secret: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise CryptoConfigurationError("crypto output name must not be empty")


@dataclass(frozen=True, slots=True)
class CryptoOutputValue[T]:
    output: CryptoOutput[T]
    value: T = field(repr=False)


@dataclass(frozen=True, slots=True)
class CryptoResult[T]:
    value: T = field(repr=False)
    outputs: tuple[CryptoOutputValue[Any], ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class CryptoValues:
    items: tuple[tuple[object, object], ...] = ()

    def input[T](self, descriptor: CryptoInput[T]) -> T:
        return cast(T, self._require(descriptor, "input", descriptor.name))

    def metadata[T](self, descriptor: CryptoOutput[T]) -> T:
        return cast(T, self._require(descriptor, "metadata", descriptor.name))

    def _require(self, descriptor: object, kind: str, name: str) -> object:
        for identity, value in self.items:
            if identity is descriptor:
                return value
        raise CryptoConfigurationError(f"crypto {kind} {name!r} is unavailable at this stage")

    def __repr__(self) -> str:
        return f"CryptoValues(count={len(self.items)})"


@dataclass(frozen=True, slots=True)
class CryptoContext:
    operation_id: str
    profile: str
    algorithm: str
    direction: CryptoDirection
    stage: CryptoStage
    attempt: int
    path: JsonPath | None = None
    aad: tuple[tuple[str, FrozenValue], ...] = field(default=(), repr=False)
    values: CryptoValues = field(default_factory=CryptoValues, repr=False)

    def input[T](self, descriptor: CryptoInput[T]) -> T:
        return self.values.input(descriptor)

    def metadata[T](self, descriptor: CryptoOutput[T]) -> T:
        return self.values.metadata(descriptor)


@dataclass(frozen=True, slots=True)
class HttpCryptoContext(CryptoContext):
    method: str = ""
    authority: str = ""
    clear_content_type: str | None = None
    outer_content_type: str | None = None


@dataclass(frozen=True, slots=True)
class WebSocketCryptoContext(CryptoContext):
    endpoint: str = field(default="", repr=False)
    protocol: str = ""
    channel: str | None = None
    event: str | None = None
    generation: int = 0
    frame_kind: str | None = None


class ValueEncryptor(Protocol):
    @property
    def name(self) -> str: ...

    def encrypt(
        self, value: FrozenValue, *, context: CryptoContext
    ) -> FrozenValue | CryptoResult[FrozenValue]: ...


class AsyncValueEncryptor(Protocol):
    @property
    def name(self) -> str: ...

    async def encrypt(
        self, value: FrozenValue, *, context: CryptoContext
    ) -> FrozenValue | CryptoResult[FrozenValue]: ...


class ValueDecryptor(Protocol):
    @property
    def name(self) -> str: ...

    def decrypt(
        self, value: FrozenValue, *, context: CryptoContext
    ) -> FrozenValue | CryptoResult[FrozenValue]: ...


class AsyncValueDecryptor(Protocol):
    @property
    def name(self) -> str: ...

    async def decrypt(
        self, value: FrozenValue, *, context: CryptoContext
    ) -> FrozenValue | CryptoResult[FrozenValue]: ...


class EncodedEncryptor(Protocol):
    @property
    def name(self) -> str: ...

    def encrypt(self, value: bytes, *, context: CryptoContext) -> bytes | CryptoResult[bytes]: ...


class AsyncEncodedEncryptor(Protocol):
    @property
    def name(self) -> str: ...

    async def encrypt(
        self, value: bytes, *, context: CryptoContext
    ) -> bytes | CryptoResult[bytes]: ...


class EncodedDecryptor(Protocol):
    @property
    def name(self) -> str: ...

    def decrypt(self, value: bytes, *, context: CryptoContext) -> bytes | CryptoResult[bytes]: ...


class AsyncEncodedDecryptor(Protocol):
    @property
    def name(self) -> str: ...

    async def decrypt(
        self, value: bytes, *, context: CryptoContext
    ) -> bytes | CryptoResult[bytes]: ...


type EncryptValueAlgorithm = ValueEncryptor | AsyncValueEncryptor
type DecryptValueAlgorithm = ValueDecryptor | AsyncValueDecryptor
type EncryptEncodedAlgorithm = EncodedEncryptor | AsyncEncodedEncryptor
type DecryptEncodedAlgorithm = EncodedDecryptor | AsyncEncodedDecryptor


class CryptoError(EazySdkError):
    """Base class for safe payload-crypto failures."""


class CryptoConfigurationError(CryptoError, ConfigurationError, ValueError):
    pass


class PayloadEncryptionError(CryptoError):
    pass


class PayloadDecryptionError(CryptoError):
    pass


class EncryptedMediaTypeMismatchError(PayloadDecryptionError):
    pass


class EncryptedFrameKindMismatchError(PayloadDecryptionError):
    pass


class CryptoLimitError(CryptoError):
    pass


class CryptoStreamingUnsupportedError(CryptoConfigurationError):
    pass


class CryptoRuntimeMismatchError(CryptoConfigurationError):
    pass


@dataclass(frozen=True, slots=True, eq=False)
class EncryptField:
    model: type[object] = field(repr=False)
    path: JsonPath
    using: EncryptValueAlgorithm = field(repr=False)
    outputs: tuple[CryptoOutput[Any], ...] = ()

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, EncryptField)
            and self.model is other.model
            and self.path == other.path
            and self.using is other.using
            and self.outputs == other.outputs
        )

    def __hash__(self) -> int:
        return hash((EncryptField, self.model, self.path, id(self.using), self.outputs))


@dataclass(frozen=True, slots=True, eq=False)
class DecryptField:
    model: type[object] = field(repr=False)
    path: JsonPath
    using: DecryptValueAlgorithm = field(repr=False)
    metadata: tuple[CryptoOutput[Any], ...] = ()

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, DecryptField)
            and self.model is other.model
            and self.path == other.path
            and self.using is other.using
            and self.metadata == other.metadata
        )

    def __hash__(self) -> int:
        return hash((DecryptField, self.model, self.path, id(self.using), self.metadata))


@dataclass(frozen=True, slots=True, eq=False)
class EncryptEncoded:
    using: EncryptEncodedAlgorithm = field(repr=False)
    max_input_bytes: int | None = None
    max_output_bytes: int | None = None
    outputs: tuple[CryptoOutput[Any], ...] = ()

    def __post_init__(self) -> None:
        _validate_limits(self.max_input_bytes, self.max_output_bytes)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, EncryptEncoded)
            and self.using is other.using
            and self.max_input_bytes == other.max_input_bytes
            and self.max_output_bytes == other.max_output_bytes
            and self.outputs == other.outputs
        )

    def __hash__(self) -> int:
        return hash(
            (
                EncryptEncoded,
                id(self.using),
                self.max_input_bytes,
                self.max_output_bytes,
                self.outputs,
            )
        )


@dataclass(frozen=True, slots=True, eq=False)
class DecryptEncoded:
    using: DecryptEncodedAlgorithm = field(repr=False)
    max_input_bytes: int | None = None
    max_output_bytes: int | None = None
    metadata: tuple[CryptoOutput[Any], ...] = ()

    def __post_init__(self) -> None:
        _validate_limits(self.max_input_bytes, self.max_output_bytes)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, DecryptEncoded)
            and self.using is other.using
            and self.max_input_bytes == other.max_input_bytes
            and self.max_output_bytes == other.max_output_bytes
            and self.metadata == other.metadata
        )

    def __hash__(self) -> int:
        return hash(
            (
                DecryptEncoded,
                id(self.using),
                self.max_input_bytes,
                self.max_output_bytes,
                self.metadata,
            )
        )


@dataclass(frozen=True, slots=True)
class OutboundCrypto:
    fields: tuple[EncryptField, ...] = ()
    encoded: EncryptEncoded | None = None

    def __post_init__(self) -> None:
        _validate_paths(tuple(item.path for item in self.fields), "outbound crypto")
        if not self.fields and self.encoded is None:
            raise CryptoConfigurationError("outbound crypto must declare a transform")


@dataclass(frozen=True, slots=True)
class InboundCrypto:
    fields: tuple[DecryptField, ...] = ()
    encoded: DecryptEncoded | None = None

    def __post_init__(self) -> None:
        _validate_paths(tuple(item.path for item in self.fields), "inbound crypto")
        if not self.fields and self.encoded is None:
            raise CryptoConfigurationError("inbound crypto must declare a transform")


@dataclass(frozen=True, slots=True)
class PayloadCrypto:
    name: str
    outbound: OutboundCrypto | None = None
    inbound: InboundCrypto | None = None
    inputs: tuple[CryptoInput[Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise CryptoConfigurationError("payload crypto profile name must not be empty")
        if self.outbound is None and self.inbound is None:
            raise CryptoConfigurationError("payload crypto profile must declare a direction")
        _validate_identities(self.inputs, "crypto inputs")


@dataclass(frozen=True, slots=True)
class HttpCryptoHeader:
    output: CryptoOutput[Any]
    name: str

    def __post_init__(self) -> None:
        if not self.name or any(character in self.name for character in "\r\n:"):
            raise CryptoConfigurationError(f"invalid crypto metadata header {self.name!r}")


@dataclass(frozen=True, slots=True)
class WebSocketCryptoField:
    output: CryptoOutput[Any]
    path: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.path or any(not item for item in self.path):
            raise CryptoConfigurationError("WebSocket crypto metadata path must not be empty")


@dataclass(frozen=True, slots=True)
class HttpEncrypted:
    content_type: str = "application/octet-stream"
    clear_content_type: str = "application/json"
    plaintext_statuses: frozenset[int] = frozenset()
    metadata: tuple[HttpCryptoHeader, ...] = ()

    def __post_init__(self) -> None:
        _validate_media_type(self.content_type, "encrypted content type")
        _validate_media_type(self.clear_content_type, "clear content type")
        if any(status < 100 or status > 599 for status in self.plaintext_statuses):
            raise CryptoConfigurationError("plaintext response statuses must be valid HTTP codes")
        _validate_binding_outputs(self.metadata, casefold=True)


@dataclass(frozen=True, slots=True)
class WebSocketEncrypted:
    frame_kind: Literal["binary", "text"] = "binary"
    text_safe: bool = False
    clear_frame_kind: Literal["text", "binary"] = "text"
    metadata: tuple[WebSocketCryptoField, ...] = ()

    def __post_init__(self) -> None:
        if self.frame_kind == "text" and not self.text_safe:
            raise CryptoConfigurationError("text encrypted frames require text_safe=True")
        _validate_binding_outputs(self.metadata, casefold=False)


type CryptoWire = HttpEncrypted | WebSocketEncrypted


@dataclass(frozen=True, slots=True)
class HttpCryptoScope:
    hosts: frozenset[str] = frozenset()
    path_prefixes: tuple[str, ...] = ()
    methods: frozenset[str] = frozenset()
    operation_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class WebSocketCryptoScope:
    endpoint_prefixes: tuple[str, ...] = ()
    operation_ids: frozenset[str] = frozenset()
    channels: frozenset[str] = frozenset()
    events: frozenset[str] = frozenset()
    directions: frozenset[CryptoDirection] = frozenset()


type CryptoScope = HttpCryptoScope | WebSocketCryptoScope


@dataclass(frozen=True, slots=True)
class CryptoRule:
    profile: PayloadCrypto
    scope: CryptoScope
    priority: int = 0
    wire: CryptoWire | None = None
    directions: frozenset[CryptoDirection] = frozenset(
        (CryptoDirection.OUTBOUND, CryptoDirection.INBOUND)
    )

    def __post_init__(self) -> None:
        if not self.directions:
            raise CryptoConfigurationError("crypto rule directions cannot be empty")


@dataclass(frozen=True, slots=True)
class ResolvedCrypto:
    profile: PayloadCrypto
    wire: CryptoWire | None


@dataclass(frozen=True, slots=True)
class CryptoRegistry:
    rules: tuple[CryptoRule, ...] = ()

    def __post_init__(self) -> None:
        names: dict[str, PayloadCrypto] = {}
        for rule in self.rules:
            existing = names.get(rule.profile.name)
            if existing is not None and existing != rule.profile:
                raise CryptoConfigurationError(
                    f"crypto profile name {rule.profile.name!r} has conflicting definitions"
                )
            names[rule.profile.name] = rule.profile

    def resolve_http(
        self,
        *,
        host: str,
        path: str,
        method: str,
        operation_id: str,
    ) -> ResolvedCrypto | None:
        matches = tuple(
            rule
            for rule in self.rules
            if isinstance(rule.scope, HttpCryptoScope)
            and _matches_http(rule.scope, host, path, method, operation_id)
        )
        return _select_rule(matches, "HTTP")

    def resolve_websocket(
        self,
        *,
        endpoint: str,
        operation_id: str,
        channel: str | None,
        event: str | None,
        direction: CryptoDirection,
    ) -> ResolvedCrypto | None:
        matches = tuple(
            rule
            for rule in self.rules
            if isinstance(rule.scope, WebSocketCryptoScope)
            and direction in rule.directions
            and _matches_websocket(
                rule.scope,
                endpoint,
                operation_id,
                channel,
                event,
                direction,
            )
        )
        return _select_rule(matches, "WebSocket")


def encrypt_field[TModel](
    model: type[TModel],
    select: Callable[[TModel], object],
    /,
    *,
    using: EncryptValueAlgorithm,
    outputs: tuple[CryptoOutput[Any], ...] = (),
) -> EncryptField:
    return EncryptField(cast(type[object], model), _selected_path(model, select), using, outputs)


def decrypt_field[TModel](
    model: type[TModel],
    select: Callable[[TModel], object],
    /,
    *,
    using: DecryptValueAlgorithm,
    metadata: tuple[CryptoOutput[Any], ...] = (),
) -> DecryptField:
    return DecryptField(cast(type[object], model), _selected_path(model, select), using, metadata)


def encrypt_encoded(
    *,
    using: EncryptEncodedAlgorithm,
    max_input_bytes: int | None = None,
    max_output_bytes: int | None = None,
    outputs: tuple[CryptoOutput[Any], ...] = (),
) -> EncryptEncoded:
    return EncryptEncoded(using, max_input_bytes, max_output_bytes, outputs)


def decrypt_encoded(
    *,
    using: DecryptEncodedAlgorithm,
    max_input_bytes: int | None = None,
    max_output_bytes: int | None = None,
    metadata: tuple[CryptoOutput[Any], ...] = (),
) -> DecryptEncoded:
    return DecryptEncoded(using, max_input_bytes, max_output_bytes, metadata)


def encrypt_outbound(
    *fields: EncryptField,
    encoded: EncryptEncoded | None = None,
) -> OutboundCrypto:
    return OutboundCrypto(fields, encoded)


def decrypt_inbound(
    *fields: DecryptField,
    encoded: DecryptEncoded | None = None,
) -> InboundCrypto:
    return InboundCrypto(fields, encoded)


def payload_crypto(
    name: str,
    *,
    outbound: OutboundCrypto | None = None,
    inbound: InboundCrypto | None = None,
    inputs: tuple[CryptoInput[Any], ...] = (),
) -> PayloadCrypto:
    return PayloadCrypto(name, outbound, inbound, inputs)


def crypto_input[T](
    dependency: RequestDependency[T],
    /,
    *,
    name: str | None = None,
    scope: CryptoInputScope = CryptoInputScope.OPERATION,
    aad: bool = True,
) -> CryptoInput[T]:
    return CryptoInput(name or dependency.diagnostic_name, dependency, scope, aad)


def crypto_output[T](
    name: str,
    annotation: type[T],
    /,
    *,
    secret: bool = False,
) -> CryptoOutput[T]:
    return CryptoOutput(name, PythonTypeValidator(annotation), secret)


def crypto_value[T](output: CryptoOutput[T], value: T, /) -> CryptoOutputValue[T]:
    return CryptoOutputValue(output, value)


def crypto_result[T](value: T, /, *outputs: CryptoOutputValue[Any]) -> CryptoResult[T]:
    return CryptoResult(value, outputs)


def http_crypto_header[T](output: CryptoOutput[T], name: str, /) -> HttpCryptoHeader:
    return HttpCryptoHeader(output, name)


def websocket_crypto_field[T](
    output: CryptoOutput[T],
    *path: str,
) -> WebSocketCryptoField:
    return WebSocketCryptoField(output, path)


def http_encrypted(
    *,
    content_type: str = "application/octet-stream",
    clear_content_type: str = "application/json",
    plaintext_statuses: frozenset[int] = frozenset(),
    metadata: tuple[HttpCryptoHeader, ...] = (),
) -> HttpEncrypted:
    return HttpEncrypted(content_type, clear_content_type, plaintext_statuses, metadata)


def websocket_encrypted(
    *,
    frame_kind: Literal["binary", "text"] = "binary",
    text_safe: bool = False,
    clear_frame_kind: Literal["text", "binary"] = "text",
    metadata: tuple[WebSocketCryptoField, ...] = (),
) -> WebSocketEncrypted:
    return WebSocketEncrypted(frame_kind, text_safe, clear_frame_kind, metadata)


def http_crypto_scope(
    *,
    hosts: tuple[str, ...] = (),
    path_prefixes: tuple[str, ...] = (),
    methods: tuple[str, ...] = (),
    operation_ids: tuple[str, ...] = (),
) -> HttpCryptoScope:
    return HttpCryptoScope(
        frozenset(item.casefold() for item in hosts),
        path_prefixes,
        frozenset(item.upper() for item in methods),
        frozenset(operation_ids),
    )


def websocket_crypto_scope(
    *,
    endpoint_prefixes: tuple[str, ...] = (),
    operation_ids: tuple[str, ...] = (),
    channels: tuple[str, ...] = (),
    events: tuple[str, ...] = (),
    directions: tuple[CryptoDirection, ...] = (),
) -> WebSocketCryptoScope:
    return WebSocketCryptoScope(
        endpoint_prefixes,
        frozenset(operation_ids),
        frozenset(channels),
        frozenset(events),
        frozenset(directions),
    )


class _PathRecorder:
    __slots__ = ("_recorder_path",)

    def __init__(self, path: JsonPath = ()) -> None:
        object.__setattr__(self, "_recorder_path", path)

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_recorder_"):
            return cast(_PathRecorder, object.__getattribute__(self, name))
        if name.startswith("__"):
            raise CryptoConfigurationError("dunder traversal is not a crypto field path")
        path = cast(JsonPath, object.__getattribute__(self, "_recorder_path"))
        return _PathRecorder((*path, name))

    def __getitem__(self, key: object) -> _PathRecorder:
        if not isinstance(key, str | int) or isinstance(key, bool):
            raise CryptoConfigurationError("crypto path indexes must be literal strings or ints")
        path = cast(JsonPath, object.__getattribute__(self, "_recorder_path"))
        return _PathRecorder((*path, key))

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise CryptoConfigurationError("method calls are not allowed in crypto field selectors")


def _selected_path[TModel](model: type[TModel], select: Callable[[TModel], object]) -> JsonPath:
    if not isinstance(model, type):
        raise CryptoConfigurationError("crypto field selector requires a model type")
    try:
        selected = select(cast(TModel, _PathRecorder()))
    except CryptoConfigurationError:
        raise
    except Exception:
        raise CryptoConfigurationError("crypto field selector is not pure path traversal") from None
    if not isinstance(selected, _PathRecorder):
        raise CryptoConfigurationError("crypto field selector must return one model path")
    path = cast(JsonPath, object.__getattribute__(selected, "_recorder_path"))
    if not path:
        raise CryptoConfigurationError("crypto field selector path cannot be empty")
    return path


def resolve_wire_path(
    model: type[object],
    path: JsonPath,
    models: ModelAdapterRegistry,
) -> JsonPath:
    """Resolve Python model attributes to the adapter's serialized field names."""

    current: object = model
    output: list[JsonPathComponent] = []
    traversed: list[str] = []
    for component in path:
        current = _unambiguous_type(current, traversed)
        origin = get_origin(current)
        args = get_args(current)
        if isinstance(component, int):
            if origin not in {list, tuple}:
                location = ".".join(traversed) or "<root>"
                raise CryptoConfigurationError(
                    f"crypto selector index requires a sequence at {location}"
                )
            if component < 0:
                raise CryptoConfigurationError("negative crypto selector indexes are unsupported")
            output.append(component)
            current = args[0] if args else object
            traversed.append(f"[{component}]")
            continue
        if origin is dict:
            output.append(component)
            current = args[-1] if args else object
            traversed.append(component)
            continue
        try:
            fields = models.fields(current)
        except Exception:
            raise CryptoConfigurationError(
                f"crypto selector cannot traverse {component!r} on unsupported type"
            ) from None
        selected = next((item for item in fields if item.name == component), None)
        if selected is None:
            owner = getattr(current, "__name__", current)
            raise CryptoConfigurationError(
                f"unknown crypto selector field {component!r} on {owner!r}"
            )
        output.append(selected.wire_name)
        current = selected.annotation
        traversed.append(component)
    return tuple(output)


def _unambiguous_type(annotation: object, traversed: list[str]) -> object:
    from types import UnionType
    from typing import Union, get_args, get_origin

    annotation, _ = unwrap_annotated(annotation)
    origin = get_origin(annotation)
    if origin in {UnionType, Union}:
        candidates = tuple(item for item in get_args(annotation) if item is not type(None))
        if len(candidates) != 1:
            raise CryptoConfigurationError(
                f"ambiguous union in crypto selector at {'.'.join(traversed) or '<root>'}"
            )
        return _unambiguous_type(candidates[0], traversed)
    return annotation


def _validate_paths(paths: tuple[JsonPath, ...], owner: str) -> None:
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            common = min(len(path), len(other))
            if path[:common] == other[:common]:
                raise CryptoConfigurationError(
                    f"{owner} paths overlap: {_path_name(path)} and {_path_name(other)}"
                )


def _path_name(path: JsonPath) -> str:
    return ".".join(str(item) for item in path)


def _validate_limits(*limits: int | None) -> None:
    if any(limit is not None and limit <= 0 for limit in limits):
        raise CryptoConfigurationError("crypto byte limits must be positive")


def _validate_media_type(value: str, label: str) -> None:
    if not value or "/" not in value or any(character.isspace() for character in value):
        raise CryptoConfigurationError(f"{label} is invalid: {value!r}")


def _validate_identities(values: tuple[object, ...], owner: str) -> None:
    identities: set[int] = set()
    names: set[str] = set()
    for value in values:
        identity = id(value)
        name = cast(str, getattr(value, "name", ""))
        if identity in identities or name in names:
            raise CryptoConfigurationError(f"duplicate {owner}: {name!r}")
        identities.add(identity)
        names.add(name)


def _validate_binding_outputs(
    bindings: tuple[HttpCryptoHeader, ...] | tuple[WebSocketCryptoField, ...],
    *,
    casefold: bool,
) -> None:
    _validate_identities(tuple(item.output for item in bindings), "crypto metadata bindings")
    slots: set[object] = set()
    for binding in bindings:
        raw: object = binding.name if isinstance(binding, HttpCryptoHeader) else binding.path
        slot = raw.casefold() if casefold and isinstance(raw, str) else raw
        if slot in slots:
            raise CryptoConfigurationError(f"duplicate crypto metadata slot: {raw!r}")
        slots.add(slot)
    websocket_paths = tuple(
        item.path for item in bindings if isinstance(item, WebSocketCryptoField)
    )
    for index, path in enumerate(websocket_paths):
        for other in websocket_paths[index + 1 :]:
            common = min(len(path), len(other))
            if path[:common] == other[:common]:
                raise CryptoConfigurationError(
                    "overlapping WebSocket crypto metadata slots: "
                    f"{'.'.join(path)} and {'.'.join(other)}"
                )


def _matches_http(
    scope: HttpCryptoScope,
    host: str,
    path: str,
    method: str,
    operation_id: str,
) -> bool:
    return (
        (not scope.hosts or host.casefold() in scope.hosts)
        and (not scope.path_prefixes or path.startswith(scope.path_prefixes))
        and (not scope.methods or method.upper() in scope.methods)
        and (not scope.operation_ids or operation_id in scope.operation_ids)
    )


def _matches_websocket(
    scope: WebSocketCryptoScope,
    endpoint: str,
    operation_id: str,
    channel: str | None,
    event: str | None,
    direction: CryptoDirection,
) -> bool:
    return (
        (not scope.endpoint_prefixes or endpoint.startswith(scope.endpoint_prefixes))
        and (not scope.operation_ids or operation_id in scope.operation_ids)
        and (not scope.channels or channel in scope.channels)
        and (not scope.events or event in scope.events)
        and (not scope.directions or direction in scope.directions)
    )


def _select_rule(matches: tuple[CryptoRule, ...], protocol: str) -> ResolvedCrypto | None:
    if not matches:
        return None
    priority = max(rule.priority for rule in matches)
    selected = tuple(rule for rule in matches if rule.priority == priority)
    if len(selected) != 1:
        names = sorted(rule.profile.name for rule in selected)
        raise CryptoConfigurationError(
            f"ambiguous {protocol} crypto rules at priority {priority}: {names}"
        )
    rule = selected[0]
    profile = PayloadCrypto(
        rule.profile.name,
        rule.profile.outbound if CryptoDirection.OUTBOUND in rule.directions else None,
        rule.profile.inbound if CryptoDirection.INBOUND in rule.directions else None,
        rule.profile.inputs,
    )
    return ResolvedCrypto(profile, rule.wire)


__all__ = [
    "AsyncEncodedDecryptor",
    "AsyncEncodedEncryptor",
    "AsyncValueDecryptor",
    "AsyncValueEncryptor",
    "CryptoConfigurationError",
    "CryptoContext",
    "CryptoDirection",
    "CryptoError",
    "CryptoInput",
    "CryptoInputScope",
    "CryptoLimitError",
    "CryptoOutput",
    "CryptoOutputValue",
    "CryptoRegistry",
    "CryptoResult",
    "CryptoRule",
    "CryptoRuntimeMismatchError",
    "CryptoStage",
    "CryptoStreamingUnsupportedError",
    "CryptoValues",
    "CryptoWire",
    "DecryptEncoded",
    "DecryptField",
    "EncodedDecryptor",
    "EncodedEncryptor",
    "EncryptEncoded",
    "EncryptField",
    "EncryptedFrameKindMismatchError",
    "EncryptedMediaTypeMismatchError",
    "FrozenArray",
    "FrozenObject",
    "FrozenValue",
    "HttpCryptoContext",
    "HttpCryptoHeader",
    "HttpCryptoScope",
    "HttpEncrypted",
    "InboundCrypto",
    "OutboundCrypto",
    "PayloadCrypto",
    "PayloadDecryptionError",
    "PayloadEncryptionError",
    "ResolvedCrypto",
    "ValueDecryptor",
    "ValueEncryptor",
    "WebSocketCryptoContext",
    "WebSocketCryptoField",
    "WebSocketCryptoScope",
    "WebSocketEncrypted",
    "crypto_input",
    "crypto_output",
    "crypto_result",
    "crypto_value",
    "decrypt_encoded",
    "decrypt_field",
    "decrypt_inbound",
    "encrypt_encoded",
    "encrypt_field",
    "encrypt_outbound",
    "freeze_value",
    "http_crypto_header",
    "http_crypto_scope",
    "http_encrypted",
    "payload_crypto",
    "resolve_wire_path",
    "thaw_value",
    "websocket_crypto_field",
    "websocket_crypto_scope",
    "websocket_encrypted",
]
