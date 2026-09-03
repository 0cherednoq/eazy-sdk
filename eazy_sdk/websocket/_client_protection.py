"""Protection snapshots, protocol auth and per-operation crypto activation."""

from __future__ import annotations

import asyncio

from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoDirection,
    CryptoInputScope,
    CryptoRegistry,
    CryptoStage,
    CryptoValues,
    PayloadCrypto,
    WebSocketCryptoContext,
    WebSocketEncrypted,
)
from eazy_sdk.crypto._inputs import resolve_crypto_inputs
from eazy_sdk.crypto._runtime import CompiledPayloadCrypto, compile_payload_crypto

from ._artifacts import (
    ChannelKey,
    ConnectionGeneration,
    CorrelationKey,
    FrozenValue,
    MessageReservedOutput,
)
from ._client_state import (
    _validate_websocket_crypto,
    _WriteItem,
    _WsClientBase,
)
from .auth import ProtocolAuth
from .protection import (
    ProtectionSnapshot,
)
from .schemas import (
    JsonPayload,
    OutboundPayload,
)


class _ProtectionMixin(_WsClientBase):
    """Protection snapshots, protocol auth and per-operation crypto activation."""

    @property
    def last_protection_snapshot(self) -> ProtectionSnapshot | None:
        return self._last_protection_snapshot

    def _compile_operation_crypto(
        self,
        profile: PayloadCrypto | None,
        wire: WebSocketEncrypted | None,
        *,
        inherit: bool,
        operation_id: str,
        channel: str | None,
        event: str | None,
        outbound: OutboundPayload,
        inbound_models: tuple[object, ...] = (),
    ) -> tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None:
        selected_profile = profile
        selected_wire = wire
        if selected_profile is None and inherit:
            configured = self.config.crypto
            if isinstance(configured, PayloadCrypto):
                selected_profile = configured
                selected_wire = selected_wire or self.config.crypto_wire
            elif isinstance(configured, CryptoRegistry):
                resolved = configured.resolve_websocket(
                    endpoint=self.endpoint,
                    operation_id=operation_id,
                    channel=channel,
                    event=event,
                    direction=CryptoDirection.OUTBOUND,
                )
                if resolved is not None:
                    selected_profile = resolved.profile
                    if resolved.wire is not None:
                        if not isinstance(resolved.wire, WebSocketEncrypted):
                            raise CryptoConfigurationError(
                                "WebSocket crypto rule requires a WebSocketEncrypted binding"
                            )
                        selected_wire = resolved.wire
        if selected_profile is None:
            return None
        actual_wire = selected_wire or self.config.crypto_wire or WebSocketEncrypted()
        outbound_model: object | None = None
        if isinstance(outbound, JsonPayload) and outbound.model is not object:
            outbound_model = outbound.model
        elif selected_profile.outbound is not None and selected_profile.outbound.fields:
            outbound_model = object
        compiled = compile_payload_crypto(
            selected_profile,
            self.config.models,
            outbound_model=outbound_model,
            inbound_models=inbound_models,
        )
        _validate_websocket_crypto(
            compiled,
            actual_wire,
            self._crypto_reserved_outputs(actual_wire),
        )
        self._activate_inbound_crypto(compiled, actual_wire)
        return compiled, actual_wire

    def _crypto_reserved_outputs(
        self, wire: WebSocketEncrypted
    ) -> tuple[MessageReservedOutput, ...]:
        protocol_paths = getattr(self.protocol, "crypto_reserved_paths", None)
        if wire.metadata and protocol_paths is None:
            raise CryptoConfigurationError(
                "WebSocket protocol must declare crypto_reserved_paths before metadata binding"
            )
        protocol_reserved = tuple(
            MessageReservedOutput(self.protocol, path) for path in protocol_paths or ()
        )
        return (*self.config.message_reserved_outputs, *protocol_reserved)

    def _activate_inbound_crypto(
        self,
        compiled: CompiledPayloadCrypto,
        wire: WebSocketEncrypted,
    ) -> None:
        inbound = compiled.profile.inbound
        if inbound is None or inbound.encoded is None:
            return
        existing = self._inbound_crypto
        if existing is not None and (
            existing[0].profile != compiled.profile or existing[1] != wire
        ):
            raise CryptoConfigurationError(
                "one WebSocket connection cannot select multiple inbound encoded crypto profiles"
            )
        self._inbound_crypto = (compiled, wire)

    async def _crypto_context(
        self,
        compiled: CompiledPayloadCrypto,
        operation_id: str,
        event: str | None,
        channel: ChannelKey | None,
        direction: CryptoDirection,
        stage: CryptoStage,
        *,
        generation: ConnectionGeneration | None = None,
        frame_kind: str | None = None,
    ) -> WebSocketCryptoContext:
        connection_inputs = tuple(
            item for item in compiled.profile.inputs if item.scope is CryptoInputScope.CONNECTION
        )
        operation_inputs = tuple(
            item
            for item in compiled.profile.inputs
            if item.scope is CryptoInputScope.OPERATION and stage is CryptoStage.DOCUMENT
        )
        connection_values, connection_aad = await resolve_crypto_inputs(
            connection_inputs,
            self.config.dependencies,
            operation_id="connection",
            attempt=(generation or self._generation).value,
            cache=self._crypto_connection_values,
        )
        operation_values, operation_aad = await resolve_crypto_inputs(
            operation_inputs,
            self.config.dependencies,
            operation_id=operation_id,
            attempt=(generation or self._generation).value,
        )
        values = CryptoValues((*connection_values.items, *operation_values.items))
        return WebSocketCryptoContext(
            operation_id,
            compiled.profile.name,
            "pending",
            direction,
            stage,
            (generation or self._generation).value,
            aad=(*connection_aad, *operation_aad),
            values=values,
            endpoint=self.endpoint,
            protocol=type(self.protocol).__name__,
            channel=channel.value if channel is not None else None,
            event=event,
            generation=(generation or self._generation).value,
            frame_kind=frame_kind,
        )

    async def _send_protocol_auth(self, application: ProtocolAuth) -> None:
        frame = await self._prepare_outbound(
            application.discriminator,
            await application.resolve(),
            correlation=None,
            channel=None,
            payload_schema=None,
        )
        completion = asyncio.get_running_loop().create_future()
        self._enqueue(_WriteItem(frame, completion), allow_handshaking=True)
        await completion
        if application.await_ready:
            await asyncio.wait_for(
                self._protocol_ready.wait(),
                timeout=application.ready_timeout,
            )

    async def _send_protocol_envelope(
        self,
        envelope: FrozenValue,
        payload: FrozenValue,
        *,
        correlation: CorrelationKey | None,
        channel: ChannelKey | None,
        operation: str,
        allow_handshaking: bool = False,
    ) -> None:
        frame = await self._prepare_envelope(
            envelope,
            payload,
            correlation=correlation,
            channel=channel,
            operation=operation,
        )
        completion = asyncio.get_running_loop().create_future()
        self._enqueue(_WriteItem(frame, completion), allow_handshaking=allow_handshaking)
        await completion
