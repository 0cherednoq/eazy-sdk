"""Zapros message normalization without connection or SDK operation knowledge."""

from __future__ import annotations

from dataclasses import dataclass

from zapros.websocket import (
    BinaryMessage,
    CloseMessage,
    Message,
    PingMessage,
    PongMessage,
    TextMessage,
)

from ._artifacts import EncodedFrame, FrameKind, InboundFrame
from .errors import FrameTooLargeError, FrameTypeError


@dataclass(frozen=True, slots=True)
class FrameLimits:
    max_text_bytes: int = 1_048_576
    max_binary_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if self.max_text_bytes <= 0 or self.max_binary_bytes <= 0:
            raise ValueError("frame limits must be positive")


DEFAULT_FRAME_LIMITS = FrameLimits()


def frame_from_zapros(
    message: Message,
    *,
    limits: FrameLimits = DEFAULT_FRAME_LIMITS,
) -> InboundFrame:
    match message:
        case TextMessage(data=data):
            size = len(data.encode("utf-8"))
            _check_size(size, limits.max_text_bytes, "text")
            return InboundFrame(FrameKind.TEXT, data)
        case BinaryMessage(data=data):
            _check_size(len(data), limits.max_binary_bytes, "binary")
            return InboundFrame(FrameKind.BINARY, data)
        case PingMessage(data=data):
            return InboundFrame(FrameKind.PING, data)
        case PongMessage(data=data):
            return InboundFrame(FrameKind.PONG, data)
        case CloseMessage(code=code, reason=reason):
            return InboundFrame(
                FrameKind.CLOSE,
                b"",
                close_code=int(code),
                close_reason=reason or "",
            )
        case _:
            raise FrameTypeError(f"unsupported Zapros message: {type(message).__name__}")


def frame_to_zapros(frame: EncodedFrame) -> TextMessage | BinaryMessage:
    if frame.kind is FrameKind.TEXT and isinstance(frame.data, str):
        return TextMessage(frame.data)
    if frame.kind is FrameKind.BINARY and isinstance(frame.data, bytes):
        return BinaryMessage(frame.data)
    raise FrameTypeError(f"unsupported encoded frame kind: {frame.kind.value}")


def _check_size(actual: int, limit: int, kind: str) -> None:
    if actual > limit:
        raise FrameTooLargeError(actual=actual, limit=limit, kind=kind)
