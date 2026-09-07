"""Review of phase 50: ``Omittable`` means the same thing on both protocols.

The HTTP side drops a field left at ``UNSET`` before the executor ever sees it. The WebSocket
side sent it verbatim, so the sentinel reached the model registry and the message failed with
``no model adapter supports GenericAlias`` instead of simply not carrying the field.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from zapros.websocket import TextMessage

from eazy_sdk import UNSET, Omittable, op
from eazy_sdk.websocket import AsyncWsApi, AsyncWsClient, JsonEventProtocol, Ws, WsSend
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True, slots=True, kw_only=True)
class Notify(WsSend):
    __ws__ = Ws.send("notify")

    value: int
    note: Omittable[str] = UNSET


class Notifications(AsyncWsApi):
    notify = op(Notify)


async def test_omitted_field_is_not_in_the_message() -> None:
    """``UNSET`` is what "not passed" looks like; an explicit value still travels."""

    connection = LiveFakeWebSocket()
    connector = FakeConnector([connection])
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=JsonEventProtocol(
                event_field="type", payload_field="data", correlation_field="id"
            ),
            connector=connector,
        ) as client,
    ):
        sdk = Notifications(client)
        await sdk.notify(value=3)
        await sdk.notify(value=4, note="hello")

    assert connection.sent == [
        TextMessage('{"data":{"value":3},"type":"notify"}'),
        TextMessage('{"data":{"note":"hello","value":4},"type":"notify"}'),
    ]
