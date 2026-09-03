"""WebSocket lowering for common payload-crypto stages."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoOutputValue,
    CryptoValues,
    EncryptedFrameKindMismatchError,
    WebSocketCryptoContext,
    WebSocketEncrypted,
)
from eazy_sdk.crypto._runtime import (
    CompiledPayloadCrypto,
    decrypt_bytes,
    decrypt_document,
    encrypt_bytes,
    encrypt_document,
)

from ._artifacts import EncodedFrame, FrameKind, FrozenValue, InboundFrame
from .protocols import ProtocolMessage


async def protect_ws_document(
    payload: FrozenValue,
    compiled: CompiledPayloadCrypto,
    *,
    context: WebSocketCryptoContext,
    outputs: list[CryptoOutputValue[Any]] | None = None,
) -> FrozenValue:
    if not compiled.outbound_fields:
        return payload
    return await encrypt_document(
        payload, compiled.outbound_fields, context=context, outputs=outputs
    )


def apply_ws_crypto_metadata(
    envelope: FrozenValue,
    wire: WebSocketEncrypted,
    outputs: list[CryptoOutputValue[Any]],
) -> FrozenValue:
    values = {id(item.output): item.value for item in outputs}
    current = envelope
    for binding in wire.metadata:
        if id(binding.output) not in values:
            continue
        current = _set_envelope_path(current, binding.path, values[id(binding.output)])
    return current


async def unprotect_ws_message(
    message: ProtocolMessage,
    compiled: CompiledPayloadCrypto,
    wire: WebSocketEncrypted,
    *,
    context: WebSocketCryptoContext,
) -> ProtocolMessage:
    if not compiled.inbound_fields:
        return message
    values = list(context.values.items)
    for binding in wire_metadata(compiled, wire, message):
        values.append(binding)
    context = replace(context, values=CryptoValues(tuple(values)))
    payload = await decrypt_document(message.payload, compiled.inbound_fields, context=context)
    return replace(message, payload=payload)


async def protect_ws_frame(
    frame: EncodedFrame,
    compiled: CompiledPayloadCrypto,
    wire: WebSocketEncrypted,
    *,
    context: WebSocketCryptoContext,
) -> EncodedFrame:
    outbound = compiled.profile.outbound
    if outbound is None or outbound.encoded is None:
        return frame
    clear = frame.data.encode("utf-8") if isinstance(frame.data, str) else frame.data
    encrypted = await encrypt_bytes(clear, outbound.encoded, context=context)
    if wire.frame_kind == "binary":
        return EncodedFrame(encrypted, FrameKind.BINARY)
    try:
        text = encrypted.decode("utf-8")
    except UnicodeDecodeError:
        raise EncryptedFrameKindMismatchError(
            "text-safe encrypted frame output is not valid UTF-8"
        ) from None
    return EncodedFrame(text, FrameKind.TEXT)


async def unprotect_ws_frame(
    frame: InboundFrame,
    compiled: CompiledPayloadCrypto,
    wire: WebSocketEncrypted,
    *,
    context: WebSocketCryptoContext,
) -> InboundFrame:
    inbound = compiled.profile.inbound
    if inbound is None or inbound.encoded is None:
        return frame
    expected = FrameKind.BINARY if wire.frame_kind == "binary" else FrameKind.TEXT
    if frame.kind is not expected:
        raise EncryptedFrameKindMismatchError(
            f"expected encrypted {wire.frame_kind} frame, got {frame.kind.value}"
        )
    encrypted = frame.data.encode("utf-8") if isinstance(frame.data, str) else frame.data
    clear = await decrypt_bytes(encrypted, inbound.encoded, context=context)
    if wire.clear_frame_kind == "binary":
        return InboundFrame(FrameKind.BINARY, clear)
    try:
        text = clear.decode("utf-8")
    except UnicodeDecodeError:
        raise CryptoConfigurationError("decrypted text frame is not valid UTF-8") from None
    return InboundFrame(FrameKind.TEXT, text)


def wire_metadata(
    compiled: CompiledPayloadCrypto,
    wire: WebSocketEncrypted,
    message: ProtocolMessage,
) -> tuple[tuple[object, object], ...]:
    inbound = compiled.profile.inbound
    required = {
        id(item)
        for declaration in (() if inbound is None else inbound.fields)
        for item in declaration.metadata
    }
    bindings = tuple(item for item in wire.metadata if id(item.output) in required)
    if not bindings:
        return ()
    if message.envelope is None:
        raise CryptoConfigurationError(
            "WebSocket protocol did not preserve the envelope required by crypto metadata"
        )
    values: list[tuple[object, object]] = []
    for binding in bindings:
        value = _read_envelope_path(message.envelope, binding.path)
        validated = binding.output.validator(value)
        values.append((binding.output, validated))
    return tuple(values)


def _set_envelope_path(root: FrozenValue, path: tuple[str, ...], value: object) -> FrozenValue:
    from eazy_sdk.crypto import freeze_value, thaw_value

    raw = thaw_value(root)
    if not isinstance(raw, dict):
        raise CryptoConfigurationError("WebSocket crypto metadata requires an object envelope")
    current = raw
    for component in path[:-1]:
        child = current.get(component)
        if child is None:
            child = {}
            current[component] = child
        if not isinstance(child, dict):
            raise CryptoConfigurationError(
                f"WebSocket crypto metadata path conflicts at {component!r}"
            )
        current = child
    final = path[-1]
    if final in current:
        raise CryptoConfigurationError(
            f"WebSocket crypto metadata path already exists: {'.'.join(path)}"
        )
    current[final] = value
    return freeze_value(raw)


def _read_envelope_path(root: FrozenValue, path: tuple[str, ...]) -> object:
    from eazy_sdk.crypto import thaw_value

    current = thaw_value(root)
    for component in path:
        if not isinstance(current, dict) or component not in current:
            raise CryptoConfigurationError(
                f"missing WebSocket crypto metadata: {'.'.join(path)}"
            )
        current = current[component]
    return current


__all__: list[str] = []
