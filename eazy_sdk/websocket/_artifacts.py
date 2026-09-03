"""Immutable message and frame artifacts for the WebSocket runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from secrets import token_bytes

from eazy_sdk.core.kernel import OperationIdentity
from eazy_sdk.crypto import (
    FrozenArray as FrozenArray,
)
from eazy_sdk.crypto import (
    FrozenObject as FrozenObject,
)
from eazy_sdk.crypto import (
    FrozenValue as FrozenValue,
)
from eazy_sdk.crypto import (
    freeze_value as freeze_value,
)
from eazy_sdk.crypto import (
    thaw_value as thaw_value,
)

_DIAGNOSTIC_SALT = token_bytes(32)


def _diagnostic_digest(content: bytes) -> str:
    return sha256(_DIAGNOSTIC_SALT + content).hexdigest()


class WsOperationKind(Enum):
    SEND = "send"
    CALL = "call"
    SUBSCRIBE = "subscribe"


class FrameKind(Enum):
    TEXT = "text"
    BINARY = "binary"
    PING = "ping"
    PONG = "pong"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class CorrelationKey:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("correlation key cannot be empty")


@dataclass(frozen=True, slots=True)
class ChannelKey:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("channel key cannot be empty")


@dataclass(frozen=True, slots=True)
class ConnectionGeneration:
    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("connection generation cannot be negative")


@dataclass(frozen=True, slots=True)
class MessageReservedOutput:
    identity: object
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LogicalMessage:
    operation: OperationIdentity
    kind: WsOperationKind
    envelope: FrozenValue
    payload: FrozenValue
    correlation: CorrelationKey | None = None
    channel: ChannelKey | None = None
    reserved_outputs: tuple[MessageReservedOutput, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class PreparedMessage:
    envelope: FrozenValue
    payload: FrozenValue
    correlation: CorrelationKey | None
    channel: ChannelKey | None
    generation: ConnectionGeneration

    def __repr__(self) -> str:
        return (
            "PreparedMessage("
            f"generation={self.generation.value}, "
            f"correlation={self.correlation!r}, channel={self.channel!r}, "
            f"envelope_type={type(self.envelope).__name__!r}, "
            f"payload_type={type(self.payload).__name__!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class EncodedFrame:
    data: str | bytes
    kind: FrameKind

    def __post_init__(self) -> None:
        if self.kind is FrameKind.TEXT and not isinstance(self.data, str):
            raise TypeError("text frame data must be str")
        if self.kind is FrameKind.BINARY and not isinstance(self.data, bytes):
            raise TypeError("binary frame data must be bytes")
        if self.kind not in {FrameKind.TEXT, FrameKind.BINARY}:
            raise ValueError("encoded application frame must be text or binary")

    def __repr__(self) -> str:
        content = self.data.encode("utf-8") if isinstance(self.data, str) else self.data
        return (
            f"EncodedFrame(kind={self.kind.value!r}, length={len(content)}, "
            f"digest={_diagnostic_digest(content)!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class InboundFrame:
    kind: FrameKind
    data: str | bytes = b""
    close_code: int | None = None
    close_reason: str | None = None

    def __repr__(self) -> str:
        content = self.data.encode("utf-8") if isinstance(self.data, str) else self.data
        return (
            f"InboundFrame(kind={self.kind.value!r}, length={len(content)}, "
            f"close_code={self.close_code!r})"
        )
