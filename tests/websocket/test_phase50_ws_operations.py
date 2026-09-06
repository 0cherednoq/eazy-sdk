"""Phase 50.4.2: a WebSocket operation is a class too, and each router carries its own kind.

The class form declares the same three verbs the decorator does — ``Ws.send``, ``Ws.call``,
``Ws.subscribe`` — and the class itself is the payload: there is no URL to place a field in,
so a placement marker on a WebSocket operation is a declaration error.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated

import pytest
from zapros.websocket import TextMessage

from eazy_sdk import Http, HttpOperation, SyncApi, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.request import markers
from eazy_sdk.websocket import (
    AsyncWsApi,
    AsyncWsClient,
    JsonEventProtocol,
    Ws,
    WsCall,
    WsSend,
    WsSubscribe,
)
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks

pytestmark = pytest.mark.asyncio


def _protocol() -> JsonEventProtocol:
    return JsonEventProtocol(event_field="type", payload_field="data", correlation_field="id")


async def _wait_for_sends(connection: LiveFakeWebSocket, count: int) -> None:
    for _ in range(100):
        if len(connection.sent) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"expected {count} sends, got {len(connection.sent)}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Notify(WsSend):
    __ws__ = Ws.send("notify")

    value: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Lookup(WsCall[str]):
    __ws__ = Ws.call("lookup")

    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Stream(WsSubscribe[int]):
    __ws__ = Ws.subscribe("subscribe")

    symbol: str


class Notifications(AsyncWsApi):
    notify = op(Notify)
    lookup = op(Lookup)
    stream = op(Stream)


async def test_ws_operation_classes_on_async_ws_api() -> None:
    """The class form sends the same frames the decorated method sends."""

    connection = LiveFakeWebSocket()
    connector = FakeConnector([connection])
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=connector,
        ) as client,
    ):
        sdk = Notifications(client)
        await sdk.notify(value=3)
        call = asyncio.create_task(sdk.lookup(name="museum"))
        await _wait_for_sends(connection, 2)
        connection.feed(TextMessage('{"type":"result","id":"1","data":"open"}'))
        assert await call == "open"

    assert connection.sent == [
        TextMessage('{"data":{"value":3},"type":"notify"}'),
        TextMessage('{"data":{"name":"museum"},"id":"1","type":"lookup"}'),
    ]


async def test_ws_operation_rejects_placements() -> None:
    """D-21: there is no URL, so a field cannot be placed anywhere but the payload."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Subscribe(WsSubscribe[int]):
        __ws__ = Ws.subscribe("subscribe")

        symbol: Annotated[str, markers.Query()]

    with pytest.raises(PlanError) as failure:
        op(Subscribe)
    assert str(failure.value) == (
        "WebSocket operation Subscribe cannot place fields; "
        "the whole class is the payload ('symbol')"
    )


async def test_ws_operation_on_http_router_rejected() -> None:
    """D-22: a WebSocket operation belongs to an ``AsyncWsApi`` and to nothing else."""

    with pytest.raises(PlanError) as failure:

        class OrdersApi(SyncApi):
            stream = op(Stream)

    assert str(failure.value) == (
        "WebSocket operation Stream is published on OrdersApi; "
        "WebSocket operations belong to AsyncWsApi"
    )


async def test_http_operation_on_ws_router_rejected() -> None:
    """D-23: the reverse is refused where it is declared, not at the first call."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class GetOrder(HttpOperation[str]):
        __http__ = Http.get("/orders/{order_id}")

        order_id: Annotated[str, markers.Path()]

    with pytest.raises(PlanError) as failure:

        class MixedApi(AsyncWsApi):
            get_order = op(GetOrder)

    assert str(failure.value) == (
        "HTTP operation get_order is published on MixedApi; "
        "an AsyncWsApi carries WebSocket operations"
    )
