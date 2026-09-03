"""One-connection async WebSocket runtime composed from lifecycle mixins."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import cast

from zapros import AsyncBaseHandler
from zapros import AsyncClient as ZaprosAsyncClient
from zapros.websocket import (
    AsyncBaseWebSocket,
    aconnect_ws,
)

from eazy_sdk.crypto import (
    PayloadCrypto,
    WebSocketEncrypted,
)
from eazy_sdk.crypto._runtime import CompiledPayloadCrypto, compile_payload_crypto

from ._artifacts import (
    ChannelKey,
    ConnectionGeneration,
    CorrelationKey,
)
from ._client_connection import _ConnectionMixin
from ._client_emit import _EmitMixin
from ._client_protection import _ProtectionMixin
from ._client_reconnect import _ReconnectMixin
from ._client_state import (
    MessageHandler,
    WsCallOptions,
    WsClientConfig,
    WsConnector,
    WsSessionState,
    _PendingExchange,
    _PendingKey,
    _SubscriptionRecord,
    _validate_websocket_crypto,
    _WriteItem,
)
from .auth import StaticUpgradeAuth
from .protection import (
    ProtectionSnapshot,
)
from .protocols import (
    WsProtocol,
)


class AsyncWsClient(_ConnectionMixin, _ReconnectMixin, _ProtectionMixin, _EmitMixin):
    """One-connection async runtime with generation-safe one-shot exchanges."""

    def __init__(
        self,
        *,
        endpoint: str,
        protocol: WsProtocol,
        zapros_client: ZaprosAsyncClient | None = None,
        zapros_handler: AsyncBaseHandler | None = None,
        upgrade_auth: StaticUpgradeAuth | None = None,
        subprotocols: tuple[str, ...] = (),
        permessage_deflate: object = False,
        config: WsClientConfig | None = None,
        connector: WsConnector | None = None,
        on_message: MessageHandler | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("WebSocket endpoint cannot be empty")
        self.endpoint = endpoint
        self.protocol = protocol
        self.subprotocols = subprotocols
        self.permessage_deflate = permessage_deflate
        self.config = config or WsClientConfig()
        if zapros_client is not None and (zapros_handler is not None or upgrade_auth is not None):
            raise ValueError("zapros_client cannot be combined with zapros_handler or upgrade_auth")
        self._zapros_client = zapros_client or ZaprosAsyncClient(
            handler=zapros_handler,
            default_headers=upgrade_auth.headers() if upgrade_auth is not None else None,
        )
        self._owns_zapros_client = zapros_client is None
        self._connector = connector or cast(WsConnector, aconnect_ws)
        self._on_message = on_message

        self._state = WsSessionState.IDLE
        self._generation = ConnectionGeneration(0)
        self._protocol_namespace = id(protocol)
        self._next_correlation = 0
        self._connect_lock = asyncio.Lock()
        self._failure_lock = asyncio.Lock()
        self._connection_context: AbstractAsyncContextManager[AsyncBaseWebSocket] | None = None
        self._websocket: AsyncBaseWebSocket | None = None
        self._writer_queue: asyncio.Queue[_WriteItem] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._heartbeat_ack = asyncio.Event()
        self._protocol_ready = asyncio.Event()
        self._active_write: _WriteItem | None = None
        self._pending: dict[_PendingKey, _PendingExchange] = {}
        self._subscriptions_by_channel: dict[tuple[int, ChannelKey], _SubscriptionRecord] = {}
        self._subscriptions_by_correlation: dict[
            tuple[int, CorrelationKey], _SubscriptionRecord
        ] = {}
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._last_protection_snapshot: ProtectionSnapshot | None = None
        self._inbound_crypto: tuple[CompiledPayloadCrypto, WebSocketEncrypted] | None = None
        self._crypto_connection_values: dict[int, object] = {}
        if isinstance(self.config.crypto, PayloadCrypto):
            default_wire = self.config.crypto_wire or WebSocketEncrypted()
            default_compiled = compile_payload_crypto(self.config.crypto, self.config.models)
            _validate_websocket_crypto(
                default_compiled,
                default_wire,
                self._crypto_reserved_outputs(default_wire),
            )
            self._activate_inbound_crypto(default_compiled, default_wire)

    @property
    def state(self) -> WsSessionState:
        return self._state

    @property
    def generation(self) -> ConnectionGeneration:
        return self._generation

    @property
    def pending_count(self) -> int:
        return len(self._pending)


__all__ = [
    "AsyncWsClient",
    "MessageHandler",
    "WsCallOptions",
    "WsClientConfig",
    "WsConnector",
    "WsSessionState",
]
