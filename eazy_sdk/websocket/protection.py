"""Immutable semantic-message and exact-frame protection stages."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from typing import Protocol

from eazy_sdk.core.kernel import Malformed, ParseAttempt, ParsedValue

from ._artifacts import (
    EncodedFrame,
    FrozenValue,
    InboundFrame,
    MessageReservedOutput,
    PreparedMessage,
    _diagnostic_digest,
    freeze_value,
    thaw_value,
)
from .protocols import ProtocolMessage


class SecretBytes:
    __slots__ = ("_value",)

    def __init__(self, value: bytes) -> None:
        self._value = bytes(value)

    def reveal(self) -> bytes:
        return self._value

    def __repr__(self) -> str:
        return "SecretBytes('[REDACTED]')"


class SecretText:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretText('[REDACTED]')"


class OutboundMessageTransform(Protocol):
    @property
    def name(self) -> str: ...

    def protect(self, message: PreparedMessage) -> PreparedMessage: ...


class InboundMessageTransform(Protocol):
    @property
    def name(self) -> str: ...

    def unprotect(self, message: ProtocolMessage) -> ParseAttempt[ProtocolMessage]: ...


class ExactFrameTransform(Protocol):
    @property
    def name(self) -> str: ...

    def protect(self, frame: EncodedFrame) -> EncodedFrame: ...

    def unprotect(self, frame: InboundFrame) -> InboundFrame: ...


@dataclass(frozen=True, slots=True)
class Timestamp:
    output_path: tuple[str, ...]
    clock: Callable[[], object]
    name: str = "timestamp"

    def protect(self, message: PreparedMessage) -> PreparedMessage:
        return _with_output(message, self.output_path, self.clock())


@dataclass(frozen=True, slots=True)
class Nonce:
    output_path: tuple[str, ...]
    factory: Callable[[], object]
    name: str = "nonce"

    def protect(self, message: PreparedMessage) -> PreparedMessage:
        return _with_output(message, self.output_path, self.factory())


@dataclass(frozen=True, slots=True)
class PerMessageAuth:
    output_path: tuple[str, ...]
    value: SecretText = dataclass_field(repr=False)
    name: str = "per-message-auth"

    def protect(self, message: PreparedMessage) -> PreparedMessage:
        return _with_output(message, self.output_path, self.value.reveal())


@dataclass(frozen=True, slots=True)
class HmacSha256MessageSignature:
    key: SecretBytes = dataclass_field(repr=False)
    output_path: tuple[str, ...] = ("signature",)
    include_paths: tuple[tuple[str, ...], ...] = ()
    name: str = "hmac-sha256-message"

    def protect(self, message: PreparedMessage) -> PreparedMessage:
        projected = _projection(message.envelope, self.output_path, self.include_paths)
        base = json.dumps(
            thaw_value(projected),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self.key.reveal(), base, hashlib.sha256).hexdigest()
        return _with_output(message, self.output_path, signature)


class CustomMessageSigner(Protocol):
    def sign(self, message: PreparedMessage) -> object: ...


@dataclass(frozen=True, slots=True)
class CustomMessageSignature:
    signer: CustomMessageSigner = dataclass_field(repr=False)
    output_path: tuple[str, ...] = ("signature",)
    name: str = "custom-message-signature"

    def protect(self, message: PreparedMessage) -> PreparedMessage:
        return _with_output(message, self.output_path, self.signer.sign(message))


@dataclass(frozen=True, slots=True)
class ProtectionSnapshot:
    generation: int
    semantic_transforms: tuple[str, ...]
    frame_transforms: tuple[str, ...]
    frame_kind: str
    frame_length: int
    frame_digest: str
    crypto_profile: str | None = None
    crypto_stages: tuple[str, ...] = ()


def apply_outbound_message_transforms(
    message: PreparedMessage,
    transforms: tuple[OutboundMessageTransform, ...],
) -> PreparedMessage:
    current = message
    sealed = False
    for transform in transforms:
        if sealed:
            raise ValueError("semantic message cannot be mutated after signature protection")
        current = transform.protect(current)
        if not isinstance(current, PreparedMessage):
            raise TypeError(f"message transform {transform.name!r} returned an invalid artifact")
        sealed = isinstance(transform, HmacSha256MessageSignature | CustomMessageSignature)
    return current


def compile_message_transforms(
    transforms: tuple[OutboundMessageTransform, ...],
) -> tuple[MessageReservedOutput, ...]:
    outputs: list[MessageReservedOutput] = []
    paths: set[tuple[str, ...]] = set()
    sealed = False
    for transform in transforms:
        if sealed:
            raise ValueError("semantic message cannot be mutated after signature protection")
        path = getattr(transform, "output_path", None)
        if path is not None:
            if (
                not isinstance(path, tuple)
                or not path
                or not all(isinstance(component, str) and component for component in path)
            ):
                raise ValueError(f"message transform {transform.name!r} has an invalid output path")
            if path in paths:
                raise ValueError(f"duplicate message protection output path: {'.'.join(path)}")
            paths.add(path)
            outputs.append(MessageReservedOutput(transform, path))
        sealed = isinstance(transform, HmacSha256MessageSignature | CustomMessageSignature)
    return tuple(outputs)


def apply_inbound_message_transforms(
    message: ProtocolMessage,
    transforms: tuple[InboundMessageTransform, ...],
) -> ParseAttempt[ProtocolMessage]:
    current = message
    for transform in transforms:
        result = transform.unprotect(current)
        if not isinstance(result, ParsedValue):
            return result
        current = result.value
    return ParsedValue(current)


def apply_outbound_frame_transforms(
    frame: EncodedFrame,
    transforms: tuple[ExactFrameTransform, ...],
) -> EncodedFrame:
    current = frame
    for transform in transforms:
        current = transform.protect(current)
        if not isinstance(current, EncodedFrame):
            raise TypeError(f"frame transform {transform.name!r} returned an invalid artifact")
    return current


def apply_inbound_frame_transforms(
    frame: InboundFrame,
    transforms: tuple[ExactFrameTransform, ...],
) -> ParseAttempt[InboundFrame]:
    current = frame
    for transform in transforms:
        try:
            current = transform.unprotect(current)
        except Exception as exc:
            return Malformed(exc)
        if not isinstance(current, InboundFrame):
            return Malformed(
                TypeError(f"frame transform {transform.name!r} returned an invalid artifact")
            )
    return ParsedValue(current)


def protection_snapshot(
    message: PreparedMessage,
    frame: EncodedFrame,
    *,
    semantic: tuple[OutboundMessageTransform, ...],
    exact: tuple[ExactFrameTransform, ...],
    crypto_profile: str | None = None,
    crypto_stages: tuple[str, ...] = (),
) -> ProtectionSnapshot:
    content = frame.data.encode("utf-8") if isinstance(frame.data, str) else frame.data
    return ProtectionSnapshot(
        message.generation.value,
        tuple(item.name for item in semantic),
        tuple(item.name for item in exact),
        frame.kind.value,
        len(content),
        _diagnostic_digest(content),
        crypto_profile,
        crypto_stages,
    )


def _with_output(
    message: PreparedMessage,
    path: tuple[str, ...],
    value: object,
) -> PreparedMessage:
    if not path:
        raise ValueError("message protection output path cannot be empty")
    updated = _set_path(message.envelope, path, freeze_value(value))
    return replace(message, envelope=updated)


def _set_path(root: FrozenValue, path: tuple[str, ...], value: FrozenValue) -> FrozenValue:
    raw = thaw_value(root)
    if not isinstance(raw, dict):
        raise TypeError("message protection requires an object envelope")
    target = raw
    for component in path[:-1]:
        child = target.get(component)
        if child is None:
            child = {}
            target[component] = child
        if not isinstance(child, dict):
            raise TypeError(f"message protection path component {component!r} is not an object")
        target = child
    target[path[-1]] = thaw_value(value)
    return freeze_value(raw)


def _projection(
    envelope: FrozenValue,
    output_path: tuple[str, ...],
    include_paths: tuple[tuple[str, ...], ...],
) -> FrozenValue:
    raw = thaw_value(envelope)
    if not isinstance(raw, dict):
        raise TypeError("message signature requires an object envelope")
    if include_paths:
        selected: dict[str, object] = {}
        for path in include_paths:
            if not path:
                raise ValueError("signature include path cannot be empty")
            value = _read_raw_path(raw, path)
            _set_raw_path(selected, path, value)
        return freeze_value(selected)
    _remove_raw_path(raw, output_path)
    return freeze_value(raw)


def _read_raw_path(root: dict[str, object], path: tuple[str, ...]) -> object:
    current: object = root
    for component in path:
        if not isinstance(current, dict) or component not in current:
            raise ValueError(f"signature projection path is missing: {'.'.join(path)}")
        current = current[component]
    return current


def _set_raw_path(root: dict[str, object], path: tuple[str, ...], value: object) -> None:
    target = root
    for component in path[:-1]:
        child = target.setdefault(component, {})
        if not isinstance(child, dict):
            raise TypeError(f"signature projection path collides at {component!r}")
        target = child
    target[path[-1]] = value


def _remove_raw_path(root: dict[str, object], path: tuple[str, ...]) -> None:
    if not path:
        return
    target = root
    for component in path[:-1]:
        child = target.get(component)
        if not isinstance(child, dict):
            return
        target = child
    target.pop(path[-1], None)


__all__ = [
    "CustomMessageSignature",
    "CustomMessageSigner",
    "ExactFrameTransform",
    "HmacSha256MessageSignature",
    "InboundMessageTransform",
    "Nonce",
    "OutboundMessageTransform",
    "PerMessageAuth",
    "ProtectionSnapshot",
    "SecretBytes",
    "SecretText",
    "Timestamp",
    "apply_inbound_frame_transforms",
    "apply_inbound_message_transforms",
    "apply_outbound_frame_transforms",
    "apply_outbound_message_transforms",
    "compile_message_transforms",
    "protection_snapshot",
]
