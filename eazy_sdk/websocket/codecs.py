"""WebSocket payload codecs independent of routing and connection state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from eazy_sdk._internal.kernel import Malformed, NoMatch, ParseAttempt, ParsedValue

from ._artifacts import EncodedFrame, FrameKind, FrozenValue, InboundFrame, freeze_value, thaw_value
from .errors import FrameTooLargeError


class WsCodec(Protocol):
    def encode(self, value: FrozenValue) -> EncodedFrame: ...

    def decode(self, frame: InboundFrame) -> ParseAttempt[FrozenValue]: ...


@dataclass(frozen=True, slots=True)
class JsonTextCodec:
    max_inbound_bytes: int = 1_048_576
    max_outbound_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if self.max_inbound_bytes <= 0 or self.max_outbound_bytes <= 0:
            raise ValueError("codec size limits must be positive")

    def encode(self, value: FrozenValue) -> EncodedFrame:
        data = json.dumps(
            thaw_value(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        size = len(data.encode("utf-8"))
        if size > self.max_outbound_bytes:
            raise FrameTooLargeError(actual=size, limit=self.max_outbound_bytes, kind="text")
        return EncodedFrame(data, FrameKind.TEXT)

    def decode(self, frame: InboundFrame) -> ParseAttempt[FrozenValue]:
        if frame.kind is not FrameKind.TEXT or not isinstance(frame.data, str):
            return NoMatch()
        size = len(frame.data.encode("utf-8"))
        if size > self.max_inbound_bytes:
            return Malformed(
                FrameTooLargeError(actual=size, limit=self.max_inbound_bytes, kind="text")
            )
        try:
            return ParsedValue(freeze_value(json.loads(frame.data)))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return Malformed(exc)
