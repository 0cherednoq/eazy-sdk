"""Composition root for separate HTTP and WebSocket runtimes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters

from .runtime import AsyncWsClient

type WsClientFactory = Callable[[str, ModelAdapterRegistry], AsyncWsClient]
type EndpointOperation = Callable[[], Awaitable[str]]


@dataclass(slots=True)
class RuntimeComposition:
    http: object
    websocket_factory: WsClientFactory = field(repr=False)
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters)
    _websocket: AsyncWsClient | None = field(default=None, init=False, repr=False)

    def websocket(self, endpoint: str) -> AsyncWsClient:
        if self._websocket is None:
            self._websocket = self.websocket_factory(endpoint, self.models)
        elif self._websocket.endpoint != endpoint:
            raise ValueError(
                "composition already owns a WebSocket runtime for a different endpoint"
            )
        return self._websocket

    async def bootstrap_websocket(self, operation: EndpointOperation) -> AsyncWsClient:
        endpoint = await operation()
        if not isinstance(endpoint, str) or not endpoint:
            raise TypeError("HTTP WebSocket bootstrap operation must return a non-empty URL")
        return self.websocket(endpoint)


__all__ = ["EndpointOperation", "RuntimeComposition", "WsClientFactory"]
