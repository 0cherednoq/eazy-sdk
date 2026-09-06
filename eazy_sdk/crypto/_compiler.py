"""Internal compiler and invocation helpers for payload crypto."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from eazy_sdk.models import ModelAdapterRegistry

from .core import (
    CryptoConfigurationError,
    CryptoContext,
    CryptoLimitError,
    CryptoOutput,
    CryptoOutputValue,
    CryptoResult,
    CryptoRuntimeMismatchError,
    DecryptEncoded,
    DecryptField,
    EncryptEncoded,
    EncryptField,
    FrozenArray,
    FrozenObject,
    FrozenValue,
    JsonPath,
    PayloadCrypto,
    PayloadDecryptionError,
    PayloadEncryptionError,
    freeze_value,
    resolve_wire_path,
    thaw_value,
)


@dataclass(frozen=True, slots=True)
class CompiledEncryptField:
    declaration: EncryptField
    wire_path: JsonPath


@dataclass(frozen=True, slots=True)
class CompiledDecryptField:
    declaration: DecryptField
    wire_path: JsonPath


@dataclass(frozen=True, slots=True)
class CompiledPayloadCrypto:
    profile: PayloadCrypto
    outbound_fields: tuple[CompiledEncryptField, ...]
    inbound_fields: tuple[CompiledDecryptField, ...]


def validate_crypto_runtime(profile: PayloadCrypto, *, allow_async: bool) -> None:
    if allow_async:
        return
    calls: list[tuple[object, str]] = []
    if profile.outbound is not None:
        calls.extend((item.using, "encrypt") for item in profile.outbound.fields)
        if profile.outbound.encoded is not None:
            calls.append((profile.outbound.encoded.using, "encrypt"))
    if profile.inbound is not None:
        calls.extend((item.using, "decrypt") for item in profile.inbound.fields)
        if profile.inbound.encoded is not None:
            calls.append((profile.inbound.encoded.using, "decrypt"))
    for algorithm, method_name in calls:
        method = getattr(algorithm, method_name, None)
        if inspect.iscoroutinefunction(method):
            name = _algorithm_name(algorithm)
            raise CryptoRuntimeMismatchError(
                f"sync HTTP runtime cannot use async crypto algorithm {name!r}"
            )


def compile_payload_crypto(
    profile: PayloadCrypto,
    models: ModelAdapterRegistry,
    *,
    outbound_model: object | None = None,
    inbound_models: tuple[object, ...] = (),
) -> CompiledPayloadCrypto:
    outbound: list[CompiledEncryptField] = []
    inbound: list[CompiledDecryptField] = []
    if profile.outbound is not None:
        for outbound_declaration in profile.outbound.fields:
            _validate_algorithm(outbound_declaration.using, "encrypt")
            _validate_model(outbound_declaration.model, outbound_model, "outbound")
            outbound.append(
                CompiledEncryptField(
                    outbound_declaration,
                    resolve_wire_path(
                        outbound_declaration.model,
                        outbound_declaration.path,
                        models,
                    ),
                )
            )
        if profile.outbound.encoded is not None:
            _validate_algorithm(profile.outbound.encoded.using, "encrypt")
    if profile.inbound is not None:
        for inbound_declaration in profile.inbound.fields:
            _validate_algorithm(inbound_declaration.using, "decrypt")
            if inbound_models and not any(
                _models_compatible(inbound_declaration.model, expected)
                for expected in inbound_models
            ):
                names = ", ".join(_type_name(item) for item in inbound_models)
                raise CryptoConfigurationError(
                    f"inbound crypto model {_type_name(inbound_declaration.model)} does not match "
                    f"operation models: {names}"
                )
            inbound.append(
                CompiledDecryptField(
                    inbound_declaration,
                    resolve_wire_path(
                        inbound_declaration.model,
                        inbound_declaration.path,
                        models,
                    ),
                )
            )
        if profile.inbound.encoded is not None:
            _validate_algorithm(profile.inbound.encoded.using, "decrypt")
    _validate_resolved_paths(tuple(item.wire_path for item in outbound), "outbound")
    _validate_resolved_paths(tuple(item.wire_path for item in inbound), "inbound")
    _validate_declared_outputs(profile)
    return CompiledPayloadCrypto(profile, tuple(outbound), tuple(inbound))


async def encrypt_document(
    document: FrozenValue,
    fields: tuple[CompiledEncryptField, ...],
    *,
    context: CryptoContext,
    outputs: list[CryptoOutputValue[Any]] | None = None,
) -> FrozenValue:
    current = document
    for field in fields:
        selected = _read_path(current, field.wire_path)
        algorithm = field.declaration.using
        name = _algorithm_name(algorithm)
        try:
            output = algorithm.encrypt(
                selected,
                context=_replace_context(context, name, field.wire_path),
            )
            if inspect.isawaitable(output):
                output = await output
            transformed = _collect_result(output, field.declaration.outputs, outputs)
            frozen = _require_frozen(transformed)
        except Exception:
            raise PayloadEncryptionError(
                f"payload encryption failed in algorithm {name!r} at document stage"
            ) from None
        current = _set_path(current, field.wire_path, frozen)
    return current


async def decrypt_document(
    document: FrozenValue,
    fields: tuple[CompiledDecryptField, ...],
    *,
    context: CryptoContext,
    outputs: list[CryptoOutputValue[Any]] | None = None,
) -> FrozenValue:
    current = document
    for field in fields:
        selected = _read_path(current, field.wire_path)
        algorithm = field.declaration.using
        name = _algorithm_name(algorithm)
        try:
            output = algorithm.decrypt(
                selected,
                context=_replace_context(context, name, field.wire_path),
            )
            if inspect.isawaitable(output):
                output = await output
            transformed = _collect_result(output, (), outputs)
            frozen = _require_frozen(transformed)
        except Exception:
            raise PayloadDecryptionError(
                f"payload decryption failed in algorithm {name!r} at document stage"
            ) from None
        current = _set_path(current, field.wire_path, frozen)
    return current


async def encrypt_bytes(
    value: bytes,
    declaration: EncryptEncoded,
    *,
    context: CryptoContext,
    outputs: list[CryptoOutputValue[Any]] | None = None,
) -> bytes:
    _check_limit(value, declaration.max_input_bytes, "encoded encryption input")
    algorithm = declaration.using
    name = _algorithm_name(algorithm)
    try:
        raw_output: object = algorithm.encrypt(value, context=_replace_context(context, name, None))
        if inspect.isawaitable(raw_output):
            raw_output = await raw_output
        transformed = _collect_result(raw_output, declaration.outputs, outputs)
        if not isinstance(transformed, bytes):
            raise TypeError("encoded encryptor must return bytes")
    except Exception:
        raise PayloadEncryptionError(
            f"payload encryption failed in algorithm {name!r} at encoded stage"
        ) from None
    _check_limit(transformed, declaration.max_output_bytes, "encoded encryption output")
    return transformed


async def decrypt_bytes(
    value: bytes,
    declaration: DecryptEncoded,
    *,
    context: CryptoContext,
    outputs: list[CryptoOutputValue[Any]] | None = None,
) -> bytes:
    _check_limit(value, declaration.max_input_bytes, "encoded decryption input")
    algorithm = declaration.using
    name = _algorithm_name(algorithm)
    try:
        raw_output: object = algorithm.decrypt(value, context=_replace_context(context, name, None))
        if inspect.isawaitable(raw_output):
            raw_output = await raw_output
        transformed = _collect_result(raw_output, (), outputs)
        if not isinstance(transformed, bytes):
            raise TypeError("encoded decryptor must return bytes")
    except Exception:
        raise PayloadDecryptionError(
            f"payload decryption failed in algorithm {name!r} at encoded stage"
        ) from None
    _check_limit(transformed, declaration.max_output_bytes, "encoded decryption output")
    return transformed


def _validate_model(declared: type[object], expected: object | None, direction: str) -> None:
    if expected is not None and not _models_compatible(declared, expected):
        raise CryptoConfigurationError(
            f"{direction} crypto model {_type_name(declared)} does not match "
            f"operation model {_type_name(expected)}"
        )


def _models_compatible(declared: type[object], expected: object) -> bool:
    return declared is expected or expected is object


def _validate_resolved_paths(paths: tuple[JsonPath, ...], direction: str) -> None:
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            length = min(len(path), len(other))
            if path[:length] == other[:length]:
                raise CryptoConfigurationError(
                    f"resolved {direction} crypto paths overlap: {path!r} and {other!r}"
                )


def _validate_declared_outputs(profile: PayloadCrypto) -> None:
    if profile.outbound is None:
        return
    declared: list[CryptoOutput[Any]] = []
    for item in profile.outbound.fields:
        declared.extend(item.outputs)
    if profile.outbound.encoded is not None:
        declared.extend(profile.outbound.encoded.outputs)
    identities: set[int] = set()
    names: set[str] = set()
    for output in declared:
        if id(output) in identities or output.name in names:
            raise CryptoConfigurationError(f"crypto output {output.name!r} has multiple writers")
        identities.add(id(output))
        names.add(output.name)


def _collect_result(
    value: object,
    declared: tuple[CryptoOutput[Any], ...],
    collector: list[CryptoOutputValue[Any]] | None,
) -> object:
    if isinstance(value, CryptoResult):
        returned = value.outputs
        transformed = value.value
    else:
        returned = ()
        transformed = value
    declared_ids = {id(item): item for item in declared}
    returned_ids: set[int] = set()
    for item in returned:
        expected = declared_ids.get(id(item.output))
        if expected is None:
            raise TypeError(f"crypto algorithm returned undeclared output {item.output.name!r}")
        if id(item.output) in returned_ids:
            raise TypeError(f"crypto algorithm returned duplicate output {item.output.name!r}")
        expected.validator(item.value)
        returned_ids.add(id(item.output))
    missing = tuple(item.name for item in declared if id(item) not in returned_ids)
    if missing:
        raise TypeError(f"crypto algorithm omitted declared outputs: {', '.join(missing)}")
    if collector is not None:
        existing = {id(item.output) for item in collector}
        if existing & returned_ids:
            raise TypeError("crypto output has multiple runtime writers")
        collector.extend(returned)
    return transformed


def _read_path(root: FrozenValue, path: JsonPath) -> FrozenValue:
    current = root
    for component in path:
        if isinstance(component, str) and isinstance(current, FrozenObject):
            match = next((value for key, value in current.items if key == component), None)
            if match is None and not any(key == component for key, _ in current.items):
                raise CryptoConfigurationError(
                    f"crypto document path is missing: {_path_name(path)}"
                )
            current = match
        elif isinstance(component, int) and isinstance(current, FrozenArray):
            try:
                current = current.items[component]
            except IndexError:
                raise CryptoConfigurationError(
                    f"crypto document path is missing: {_path_name(path)}"
                ) from None
        else:
            raise CryptoConfigurationError(
                f"crypto document path cannot traverse {_path_name(path)}"
            )
    return current


def _set_path(root: FrozenValue, path: JsonPath, value: FrozenValue) -> FrozenValue:
    raw = thaw_value(root)
    current: object = raw
    for component in path[:-1]:
        if isinstance(component, str) and isinstance(current, dict):  # noqa: SIM114
            current = current[component]
        elif isinstance(component, int) and isinstance(current, list):
            current = current[component]
        else:
            raise CryptoConfigurationError(
                f"crypto document path cannot traverse {_path_name(path)}"
            )
    final = path[-1]
    if isinstance(final, str) and isinstance(current, dict):  # noqa: SIM114
        current[final] = thaw_value(value)
    elif isinstance(final, int) and isinstance(current, list):
        current[final] = thaw_value(value)
    else:
        raise CryptoConfigurationError(f"crypto document path cannot write {_path_name(path)}")
    return freeze_value(raw)


def _replace_context(
    context: CryptoContext,
    algorithm: str,
    path: JsonPath | None,
) -> CryptoContext:
    from dataclasses import replace

    return replace(context, algorithm=algorithm, path=path)


def _algorithm_name(algorithm: object) -> str:
    name = getattr(algorithm, "name", None)
    if not isinstance(name, str) or not name:
        raise CryptoConfigurationError("crypto algorithm requires a non-empty name")
    return name


def _validate_algorithm(algorithm: object, method_name: str) -> None:
    name = _algorithm_name(algorithm)
    if not callable(getattr(algorithm, method_name, None)):
        raise CryptoConfigurationError(
            f"crypto algorithm {name!r} has no callable {method_name} method"
        )


def _require_frozen(value: object) -> FrozenValue:
    if value is None or isinstance(value, bool | int | float | str | FrozenArray | FrozenObject):
        return value
    raise TypeError("field crypto algorithm must return FrozenValue")


def _check_limit(value: bytes, limit: int | None, stage: str) -> None:
    if limit is not None and len(value) > limit:
        raise CryptoLimitError(f"{stage} exceeds configured limit {limit}")


def _path_name(path: JsonPath) -> str:
    return ".".join(str(item) for item in path)


def _type_name(value: object) -> str:
    return getattr(value, "__qualname__", repr(value))


__all__: list[str] = []
