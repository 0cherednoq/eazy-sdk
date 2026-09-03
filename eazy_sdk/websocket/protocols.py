"""Pure WebSocket protocol contracts and an explicit JSON event protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, cast

from eazy_sdk.core.kernel import Malformed, NoMatch, ParseAttempt, ParsedValue

from ._artifacts import (
    ChannelKey,
    CorrelationKey,
    FrameKind,
    FrozenValue,
    InboundFrame,
    freeze_value,
    thaw_value,
)
from .codecs import JsonTextCodec, WsCodec
from .errors import ProtocolConfigurationError, ProtocolEnvelopeError


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


class CloseDisposition(Enum):
    NORMAL = "normal"
    RECONNECT = "reconnect"
    FATAL = "fatal"


@dataclass(frozen=True, slots=True)
class ProtocolMessage:
    kind: InboundMessageKind
    discriminator: str | None
    payload: FrozenValue
    correlation: CorrelationKey | None = None
    channel: ChannelKey | None = None
    control: ControlKind | None = None
    terminal_error: Exception | None = None
    envelope: FrozenValue | None = field(default=None, repr=False)


class WsProtocol(Protocol):
    @property
    def codec(self) -> WsCodec: ...

    def build_outbound(
        self,
        discriminator: str,
        payload: FrozenValue,
        *,
        correlation: CorrelationKey | None = None,
        channel: ChannelKey | None = None,
    ) -> FrozenValue: ...

    def inspect(self, frame: InboundFrame) -> ParseAttempt[ProtocolMessage]: ...

    def classify_close(self, code: int | None) -> CloseDisposition: ...

    def build_recovery(self, channel: ChannelKey, token: FrozenValue) -> FrozenValue | None: ...

    def build_cancel(
        self,
        correlation: CorrelationKey | None,
        channel: ChannelKey | None,
    ) -> FrozenValue | None: ...

    def build_control(self, kind: ControlKind, payload: FrozenValue) -> FrozenValue | None: ...


@dataclass(frozen=True, slots=True)
class ControlEvent:
    discriminator: str
    kind: ControlKind


@dataclass(frozen=True, slots=True)
class JsonEventProtocol:
    event_field: str
    payload_field: str
    correlation_field: str | None = None
    channel_field: str | None = None
    controls: tuple[ControlEvent, ...] = ()
    recovery_event: str | None = None
    recovery_token_field: str | None = None
    fatal_close_codes: frozenset[int] = frozenset()
    reconnect_close_codes: frozenset[int] = frozenset()
    codec: WsCodec = field(default_factory=JsonTextCodec)

    def __post_init__(self) -> None:
        names = [self.event_field, self.payload_field]
        names.extend(
            name for name in (self.correlation_field, self.channel_field) if name is not None
        )
        if any(not name for name in names):
            raise ProtocolConfigurationError("protocol field names cannot be empty")
        if len(names) != len(set(names)):
            raise ProtocolConfigurationError("protocol field names must be distinct")
        discriminators = [item.discriminator for item in self.controls]
        if len(discriminators) != len(set(discriminators)):
            raise ProtocolConfigurationError("control discriminators must be unique")
        if (self.recovery_event is None) != (self.recovery_token_field is None):
            raise ProtocolConfigurationError(
                "recovery_event and recovery_token_field must be configured together"
            )
        if self.recovery_event is not None and self.channel_field is None:
            raise ProtocolConfigurationError("recovery requires channel_field")
        if self.fatal_close_codes & self.reconnect_close_codes:
            raise ProtocolConfigurationError("fatal and reconnect close codes cannot overlap")

    @property
    def crypto_reserved_paths(self) -> tuple[tuple[str, ...], ...]:
        return tuple(
            (item,)
            for item in (
                self.event_field,
                self.payload_field,
                self.correlation_field,
                self.channel_field,
            )
            if item is not None
        )

    def build_outbound(
        self,
        discriminator: str,
        payload: FrozenValue,
        *,
        correlation: CorrelationKey | None = None,
        channel: ChannelKey | None = None,
    ) -> FrozenValue:
        value: dict[str, object] = {
            self.event_field: discriminator,
            self.payload_field: thaw_value(payload),
        }
        if correlation is not None:
            if self.correlation_field is None:
                raise ProtocolEnvelopeError("correlation key is not configured")
            value[self.correlation_field] = correlation.value
        if channel is not None:
            if self.channel_field is None:
                raise ProtocolEnvelopeError("channel key is not configured")
            value[self.channel_field] = channel.value
        return freeze_value(value)

    def inspect(self, frame: InboundFrame) -> ParseAttempt[ProtocolMessage]:
        if frame.kind is FrameKind.PING:
            return ParsedValue(self._control(ControlKind.PING, frame.data))
        if frame.kind is FrameKind.PONG:
            return ParsedValue(self._control(ControlKind.PONG, frame.data))
        if frame.kind is FrameKind.CLOSE:
            return ParsedValue(self._control(ControlKind.CLOSE, frame.close_reason or ""))
        decoded = self.codec.decode(frame)
        if not isinstance(decoded, ParsedValue):
            return decoded
        raw = thaw_value(decoded.value)
        if not isinstance(raw, dict):
            return Malformed(ProtocolEnvelopeError("JSON event envelope must be an object"))
        discriminator = raw.get(self.event_field)
        if not isinstance(discriminator, str):
            return NoMatch()
        if self.payload_field not in raw:
            return Malformed(ProtocolEnvelopeError("JSON event envelope has no payload field"))
        try:
            payload = freeze_value(raw[self.payload_field])
            correlation = self._key(raw, self.correlation_field, CorrelationKey)
            channel = self._key(raw, self.channel_field, ChannelKey)
        except (TypeError, ValueError) as exc:
            return Malformed(ProtocolEnvelopeError(str(exc)))
        control = next(
            (item.kind for item in self.controls if item.discriminator == discriminator),
            None,
        )
        if control is not None:
            kind = InboundMessageKind.CONTROL
        elif correlation is not None:
            kind = InboundMessageKind.REPLY
        elif channel is not None:
            kind = InboundMessageKind.EVENT
        else:
            kind = InboundMessageKind.MESSAGE
        return ParsedValue(
            ProtocolMessage(
                kind,
                discriminator,
                payload,
                correlation,
                channel,
                control,
                envelope=decoded.value,
            )
        )

    def classify_close(self, code: int | None) -> CloseDisposition:
        if code in self.fatal_close_codes:
            return CloseDisposition.FATAL
        if code in self.reconnect_close_codes:
            return CloseDisposition.RECONNECT
        return CloseDisposition.NORMAL if code == 1000 else CloseDisposition.RECONNECT

    def build_recovery(self, channel: ChannelKey, token: FrozenValue) -> FrozenValue | None:
        if self.recovery_event is None or self.recovery_token_field is None:
            return None
        return self.build_outbound(
            self.recovery_event,
            freeze_value({self.recovery_token_field: thaw_value(token)}),
            channel=channel,
        )

    def build_cancel(
        self,
        correlation: CorrelationKey | None,
        channel: ChannelKey | None,
    ) -> FrozenValue | None:
        return None

    def build_control(self, kind: ControlKind, payload: FrozenValue) -> FrozenValue | None:
        return None

    @staticmethod
    def _key[TKey: CorrelationKey | ChannelKey](
        raw: dict[str, object],
        field: str | None,
        key_type: type[TKey],
    ) -> TKey | None:
        if field is None or field not in raw:
            return None
        value = raw[field]
        if not isinstance(value, str):
            raise TypeError(f"{field!r} must be a string")
        return cast(TKey, key_type(value))

    @staticmethod
    def _control(kind: ControlKind, payload: str | bytes) -> ProtocolMessage:
        return ProtocolMessage(
            InboundMessageKind.CONTROL,
            None,
            freeze_value(
                payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
            ),
            control=kind,
        )
