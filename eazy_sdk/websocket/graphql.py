"""First-party implementation of the ``graphql-transport-ws`` protocol."""

from __future__ import annotations

from dataclasses import dataclass, field

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
from .errors import GraphqlOperationError, ProtocolEnvelopeError
from .protocols import (
    CloseDisposition,
    ControlKind,
    InboundMessageKind,
    ProtocolMessage,
)


@dataclass(frozen=True, slots=True)
class GraphqlTransportWsProtocol:
    """Pure envelope mapping for the GraphQL over WebSocket protocol."""

    codec: WsCodec = field(default_factory=JsonTextCodec)
    fatal_close_codes: frozenset[int] = frozenset({4400, 4401, 4403, 4406, 4409, 4429})

    @property
    def crypto_reserved_paths(self) -> tuple[tuple[str, ...], ...]:
        return (("type",), ("payload",), ("id",))

    def build_outbound(
        self,
        discriminator: str,
        payload: FrozenValue,
        *,
        correlation: CorrelationKey | None = None,
        channel: ChannelKey | None = None,
    ) -> FrozenValue:
        if channel is not None:
            raise ProtocolEnvelopeError("graphql-transport-ws does not use channel keys")
        if discriminator == "subscribe" and correlation is None:
            raise ProtocolEnvelopeError("GraphQL subscribe requires an operation ID")
        if discriminator not in {"connection_init", "ping", "pong", "subscribe"}:
            raise ProtocolEnvelopeError(
                f"unsupported graphql-transport-ws outbound message: {discriminator}"
            )
        envelope: dict[str, object] = {
            "type": discriminator,
            "payload": thaw_value(payload),
        }
        if correlation is not None:
            envelope["id"] = correlation.value
        return freeze_value(envelope)

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
            return Malformed(ProtocolEnvelopeError("GraphQL message must be an object"))
        discriminator = raw.get("type")
        if not isinstance(discriminator, str):
            return NoMatch()
        if discriminator not in {"connection_ack", "ping", "pong", "next", "error", "complete"}:
            return NoMatch()
        payload = freeze_value(raw.get("payload"))
        if discriminator == "connection_ack":
            return ParsedValue(
                ProtocolMessage(
                    InboundMessageKind.CONTROL,
                    discriminator,
                    payload,
                    control=ControlKind.READY,
                    envelope=decoded.value,
                )
            )
        if discriminator in {"ping", "pong"}:
            return ParsedValue(
                ProtocolMessage(
                    InboundMessageKind.CONTROL,
                    discriminator,
                    payload,
                    control=(ControlKind.PING if discriminator == "ping" else ControlKind.PONG),
                    envelope=decoded.value,
                )
            )
        operation_id = raw.get("id")
        if not isinstance(operation_id, str) or not operation_id:
            return Malformed(
                ProtocolEnvelopeError(f"GraphQL {discriminator} requires a non-empty operation ID")
            )
        correlation = CorrelationKey(operation_id)
        if discriminator == "complete":
            return ParsedValue(
                ProtocolMessage(
                    InboundMessageKind.CONTROL,
                    discriminator,
                    payload,
                    correlation=correlation,
                    control=ControlKind.COMPLETE,
                    envelope=decoded.value,
                )
            )
        if "payload" not in raw:
            return Malformed(ProtocolEnvelopeError(f"GraphQL {discriminator} requires a payload"))
        terminal_error = (
            GraphqlOperationError(thaw_value(payload)) if discriminator == "error" else None
        )
        return ParsedValue(
            ProtocolMessage(
                InboundMessageKind.REPLY,
                discriminator,
                payload,
                correlation=correlation,
                terminal_error=terminal_error,
                envelope=decoded.value,
            )
        )

    def classify_close(self, code: int | None) -> CloseDisposition:
        if code in self.fatal_close_codes:
            return CloseDisposition.FATAL
        return CloseDisposition.NORMAL if code == 1000 else CloseDisposition.RECONNECT

    def build_recovery(self, channel: ChannelKey, token: FrozenValue) -> FrozenValue | None:
        return None

    def build_cancel(
        self,
        correlation: CorrelationKey | None,
        channel: ChannelKey | None,
    ) -> FrozenValue | None:
        if channel is not None or correlation is None:
            return None
        return freeze_value({"id": correlation.value, "type": "complete"})

    def build_control(self, kind: ControlKind, payload: FrozenValue) -> FrozenValue | None:
        if kind is not ControlKind.PONG:
            return None
        return freeze_value({"type": "pong", "payload": thaw_value(payload)})

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


__all__ = ["GraphqlTransportWsProtocol"]
