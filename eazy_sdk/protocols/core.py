"""Application-level protocol envelopes, independent of the transport underneath.

An envelope is the framing a service puts around a payload: JSON-RPC's
``{"jsonrpc", "id", "method", "params"}``, a ``{"event", "data"}`` message, GraphQL's
``{"query", "variables"}``. None of that is a property of WebSocket, yet all of it used to live
in ``eazy_sdk/websocket/``, so an HTTP service whose method name travels in the body had to
hand-assemble the envelope inside every business projection.

The envelope is a pure function in both directions — ``(discriminator, payload, correlation)`` to
a structure and back — with no I/O and no state beyond what it declares. That restriction is what
keeps it from growing into a second client: retries, timeouts and reconnection stay where they
already are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from eazy_sdk.core.kernel import ParseAttempt
from eazy_sdk.crypto import FrozenValue


@dataclass(frozen=True, slots=True)
class CorrelationKey:
    """What ties one reply to the request that asked for it — a JSON-RPC ``id``, say."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("correlation key cannot be empty")


@dataclass(frozen=True, slots=True)
class ChannelKey:
    """What ties a stream of messages together, where the protocol has streams."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("channel key cannot be empty")


class InboundMessageKind(Enum):
    MESSAGE = "message"
    REPLY = "reply"
    EVENT = "event"
    CONTROL = "control"


class ControlKind(Enum):
    READY = "ready"
    PING = "ping"
    PONG = "pong"
    CLOSE = "close"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class ProtocolMessage:
    """One inbound message as the envelope reads it, before any model is involved."""

    kind: InboundMessageKind
    discriminator: str | None
    payload: FrozenValue
    correlation: CorrelationKey | None = None
    channel: ChannelKey | None = None
    control: ControlKind | None = None
    terminal_error: Exception | None = None
    envelope: FrozenValue | None = field(default=None, repr=False)


class Envelope(Protocol):
    """Framing in both directions, with nothing transport-specific in the signature."""

    def build_outbound(
        self,
        discriminator: str,
        payload: FrozenValue,
        *,
        correlation: CorrelationKey | None = None,
        channel: ChannelKey | None = None,
    ) -> FrozenValue: ...

    def read(self, envelope: FrozenValue) -> ParseAttempt[ProtocolMessage]: ...


__all__ = [
    "ChannelKey",
    "ControlKind",
    "CorrelationKey",
    "Envelope",
    "InboundMessageKind",
    "ProtocolMessage",
]
