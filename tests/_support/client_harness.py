from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx
import requests
from curl_cffi import requests as curl_requests

from eazy_sdk import AsyncClient, Client, ClientConfig
from eazy_sdk.auth import Auth
from eazy_sdk.clients import CallOptions
from eazy_sdk.response import NormalizedResponse
from tests._support.zapros_clients import (
    client_from_curl_cffi,
    client_from_httpx,
    client_from_requests,
)

CLIENT_ADAPTERS: tuple[str, ...] = (
    "httpx-sync",
    "httpx-async",
    "requests",
    "curl-cffi-sync",
    "curl-cffi-async",
)


class HarnessOperation(Protocol):
    async def run_async(
        self, client: AsyncClient, options: CallOptions | None
    ) -> NormalizedResponse[object]: ...

    def run_sync(
        self, client: Client, options: CallOptions | None
    ) -> NormalizedResponse[object]: ...


@dataclass(frozen=True, slots=True)
class ClientHarness:
    """Inject one transport implementation behind a common async test interface."""

    name: str

    async def execute(
        self,
        operation: HarnessOperation,
        auth: Auth | None = None,
        *,
        options: CallOptions | None = None,
    ) -> NormalizedResponse[object]:
        config = ClientConfig(auth=auth)
        if self.name == "httpx-async":
            raw_async = httpx.AsyncClient(headers={}, cookies={})
            async with client_from_httpx(raw_async, config=config) as client:
                return await operation.run_async(client, options)
        if self.name == "curl-cffi-async":
            raw_curl_async = curl_requests.AsyncSession()
            client = client_from_curl_cffi(raw_curl_async, config=config)
            async with client:
                return await operation.run_async(client, options)

        def run_sync() -> NormalizedResponse[object]:
            if self.name == "httpx-sync":
                raw: Any = httpx.Client(headers={}, cookies={})
                client_sync = cast(Client, client_from_httpx(raw, config=config))
            elif self.name == "requests":
                raw = requests.Session()
                raw.trust_env = False
                raw.headers.clear()
                raw.cookies.clear()
                client_sync = client_from_requests(raw, config=config)
            else:
                raw = curl_requests.Session()
                client_sync = cast(Client, client_from_curl_cffi(raw, config=config))
            with client_sync:
                return operation.run_sync(client_sync, options)

        return await asyncio.to_thread(run_sync)


__all__ = ["CLIENT_ADAPTERS", "ClientHarness", "HarnessOperation"]
