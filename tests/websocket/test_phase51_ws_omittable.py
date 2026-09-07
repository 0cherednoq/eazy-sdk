"""Review of phase 50: ``Omittable`` on both protocols, and D-23 by type rather than by shape.

The HTTP side drops a field left at ``UNSET`` before the executor ever sees it. The WebSocket
side sent it verbatim, so the sentinel reached the model registry and the message failed with
``no model adapter supports GenericAlias`` instead of simply not carrying the field.

D-23 recognised an HTTP operation by two attribute names, so anything carrying ``spec`` and
``operation_type`` was reported as one.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from zapros.websocket import TextMessage

from eazy_sdk import UNSET, Http, HttpOperation, Omittable, op
from eazy_sdk.core.errors import PlanError
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


class Marker:
    """Carries the two attribute names D-23 used to duck-type an HTTP descriptor."""

    spec = "not an operation"
    operation_type = str


async def test_an_object_shaped_like_an_http_descriptor_is_not_one() -> None:
    """A router member is refused for being an HTTP operation, not for its attribute names."""

    class Notifications2(AsyncWsApi):
        marker = Marker()
        notify = op(Notify)

    assert isinstance(Notifications2.marker, Marker)


async def test_a_real_http_operation_is_still_refused() -> None:
    """D-23 itself is unchanged."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class GetOrder(HttpOperation[str]):
        __http__ = Http.get("/orders")

    with pytest.raises(PlanError) as failure:

        class Mixed(AsyncWsApi):
            get_order = op(GetOrder)

    assert str(failure.value) == (
        "HTTP operation get_order is published on Mixed; "
        "an AsyncWsApi carries WebSocket operations"
    )
