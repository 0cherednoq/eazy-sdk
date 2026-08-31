from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from wsproto import WSConnection
from wsproto.connection import ConnectionType
from wsproto.events import (
    AcceptConnection,
    CloseConnection,
)
from wsproto.events import (
    Request as WsRequest,
)
from wsproto.events import (
    TextMessage as WsTextMessage,
)
from zapros import AsgiHandler, AsyncBaseHandler, Request, Response
from zapros.websocket import TextMessage

from eazy_sdk.websocket import (
    AsyncWsClient,
    JsonEventProtocol,
    SecretText,
    StaticUpgradeAuth,
    WsConnectError,
)
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks

type Responder = Callable[[], Awaitable[None]]


def _protocol() -> JsonEventProtocol:
    return JsonEventProtocol(
        event_field="type",
        payload_field="data",
        correlation_field="id",
        channel_field="topic",
    )


def _reply(raw: str) -> str:
    request = json.loads(raw)
    return json.dumps(
        {"type": "result", "id": request["id"], "data": request["data"]},
        separators=(",", ":"),
        sort_keys=True,
    )


async def _wait_for_fake(connection: LiveFakeWebSocket) -> None:
    for _ in range(400):
        if connection.sent:
            message = connection.sent[0]
            assert isinstance(message, TextMessage)
            connection.feed(TextMessage(_reply(message.data)))
            return
        await asyncio.sleep(0)
    raise AssertionError("scripted WebSocket did not receive a call")


@asynccontextmanager
async def _fake_path() -> AsyncIterator[tuple[AsyncWsClient, Responder]]:
    connection = LiveFakeWebSocket()
    client = AsyncWsClient(
        endpoint="ws://fake.test/socket",
        protocol=_protocol(),
        connector=FakeConnector([connection]),
    )
    try:
        yield client, lambda: _wait_for_fake(connection)
    finally:
        await client.aclose()


def _echo_asgi_app(headers_seen: list[dict[str, str]]) -> Callable[..., Awaitable[None]]:
    async def app(
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        assert scope["type"] == "websocket"
        headers_seen.append({name.decode(): value.decode() for name, value in scope["headers"]})
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        while True:
            event = await receive()
            if event["type"] == "websocket.disconnect":
                return
            if event["type"] == "websocket.receive":
                await send({"type": "websocket.send", "text": _reply(event["text"])})

    return app


@asynccontextmanager
async def _asgi_path() -> AsyncIterator[tuple[AsyncWsClient, Responder]]:
    headers_seen: list[dict[str, str]] = []
    handler = AsgiHandler(_echo_asgi_app(headers_seen), enable_lifespan=False)
    client = AsyncWsClient(
        endpoint="ws://asgi.test/socket",
        protocol=_protocol(),
        zapros_handler=handler,
        upgrade_auth=StaticUpgradeAuth({"authorization": SecretText("Bearer asgi-token")}),
    )
    try:
        yield client, _no_response_action
        assert headers_seen[0]["authorization"] == "Bearer asgi-token"
    finally:
        await client.aclose()


async def _no_response_action() -> None:
    return None


@asynccontextmanager
async def _localhost_wsproto_server() -> AsyncIterator[str]:
    sessions: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            sessions.add(task)
        connection = WSConnection(ConnectionType.SERVER)
        fragments: list[str] = []
        try:
            while data := await reader.read(65536):
                connection.receive_data(data)
                close = False
                for event in connection.events():
                    if isinstance(event, WsRequest):
                        writer.write(connection.send(AcceptConnection()))
                    elif isinstance(event, WsTextMessage):
                        fragments.append(event.data)
                        if event.message_finished:
                            writer.write(
                                connection.send(WsTextMessage(data=_reply("".join(fragments))))
                            )
                            fragments.clear()
                    elif isinstance(event, CloseConnection):
                        writer.write(connection.send(event.response()))
                        close = True
                await writer.drain()
                if close:
                    return
        finally:
            writer.close()
            await writer.wait_closed()
            if task is not None:
                sessions.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    address = server.sockets[0].getsockname()
    try:
        yield f"ws://127.0.0.1:{address[1]}/socket"
    finally:
        server.close()
        await server.wait_closed()
        if sessions:
            await asyncio.gather(*tuple(sessions), return_exceptions=True)


@asynccontextmanager
async def _network_path() -> AsyncIterator[tuple[AsyncWsClient, Responder]]:
    async with _localhost_wsproto_server() as endpoint:
        client = AsyncWsClient(endpoint=endpoint, protocol=_protocol())
        try:
            yield client, _no_response_action
        finally:
            await client.aclose()


@pytest.mark.parametrize("path", ["fake", "asgi", "network"])
async def test_fake_and_production_zapros_paths_share_call_behavior(path: str) -> None:
    factories = {
        "fake": _fake_path,
        "asgi": _asgi_path,
        "network": _network_path,
    }
    async with assert_no_task_leaks(), factories[path]() as (client, respond):
        call = asyncio.create_task(client.call("echo", {"value": path}))
        await respond()
        assert await call == {"value": path}


class _UnsupportedHandoffHandler(AsyncBaseHandler):
    async def ahandle(self, request: Request) -> Response:
        key = request.headers["sec-websocket-key"]
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        return Response(
            101,
            headers={
                "connection": "Upgrade",
                "upgrade": "websocket",
                "sec-websocket-accept": accept,
            },
            context={},
            request=request,
        )

    async def aclose(self) -> None:
        return None


async def test_unsupported_zapros_handoff_becomes_typed_connect_error() -> None:
    client = AsyncWsClient(
        endpoint="ws://unsupported.test/socket",
        protocol=_protocol(),
        zapros_handler=_UnsupportedHandoffHandler(),
    )
    async with assert_no_task_leaks():
        with pytest.raises(WsConnectError) as captured:
            await client.connect()
        assert captured.value.__cause__ is not None
        assert "no network stream" in str(captured.value.__cause__)
        await client.aclose()
