"""Deterministic local transport for the remaining core-guide examples."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field

import httpx

from eazy_sdk import AsyncClient, Client, ClientConfig
from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler


@dataclass(slots=True)
class GuideSite:
    calls: list[str] = field(default_factory=list)
    health_calls: int = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.host}{request.url.path}")
        path = request.url.path
        if path in {"/device", "/documents", "/middleware", "/messages/42"}:
            return self._content(request)
        if path in {"/health", "/redirect", "/final", "/rate"}:
            return self._reliability(request)
        if path in {"/books/7", "/cards", "/rpc"}:
            return self._services(request)
        return httpx.Response(404, json={"error": "route_not_found"})

    def _content(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/device":
            return httpx.Response(200, json={"value": request.headers.get("X-Device-Id", "")})
        if path == "/documents":
            documents = ["Иск", "Отзыв", "Решение"]
            page = int(request.url.params.get("page", "1"))
            size = int(request.url.params.get("per_page", "2"))
            return httpx.Response(
                200,
                json={
                    "items": documents[(page - 1) * size : page * size],
                    "pages_count": 2,
                },
            )
        if path == "/middleware":
            return httpx.Response(200, json={"value": "ready"})
        return httpx.Response(200, json={"id": 42, "subject": "План на пятницу"})

    def _reliability(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            self.health_calls += 1
            if self.health_calls == 1:
                return httpx.Response(503, json={"code": "warming_up"})
            return httpx.Response(200, json={"value": "ready"})
        if path == "/redirect":
            return httpx.Response(303, headers={"Location": "/final"})
        if path == "/final":
            return httpx.Response(200, json={"value": "redirected"})
        if path == "/rate":
            return httpx.Response(200, json={"value": "limited"})

        return httpx.Response(404, json={"error": "route_not_found"})

    def _services(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/books/7":
            return httpx.Response(200, json={"value": "SDK Patterns"})
        if path == "/cards":
            return httpx.Response(201, json={"value": "card-42"})
        if path == "/rpc":
            document = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": document["id"],
                    "result": {"reference": "receipt-42"},
                },
            )
        return httpx.Response(404, json={"error": "route_not_found"})


@contextmanager
def guide_client(
    site: GuideSite,
    *,
    config: ClientConfig | None = None,
) -> Iterator[Client]:
    raw = httpx.Client(transport=httpx.MockTransport(site), headers={}, cookies={})
    with Client(
        base_url="https://api.mail.example",
        handler=HttpxHandler(raw, owns_client=True),
        config=config,
    ) as client:
        yield client


@asynccontextmanager
async def async_guide_client(site: GuideSite) -> AsyncIterator[AsyncClient]:
    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    async with AsyncClient(
        base_url="https://api.mail.example",
        handler=AsyncHttpxHandler(raw, owns_client=True),
    ) as client:
        yield client
