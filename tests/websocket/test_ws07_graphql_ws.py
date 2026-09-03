from __future__ import annotations

import asyncio
import json

import pytest
from zapros.websocket import CloseMessage, TextMessage

from eazy_sdk.core.kernel import ParsedValue
from eazy_sdk.websocket import (
    AsyncWsClient,
    CloseDisposition,
    ControlKind,
    GraphqlOperationError,
    GraphqlTransportWsProtocol,
    InboundMessageKind,
    ProtocolAuth,
    ReconnectPolicy,
    ResubscribeFromStart,
    SubscriptionDisconnectedError,
    WsClientConfig,
    frame_from_zapros,
    freeze_value,
    thaw_value,
)
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks


async def _wait_for_sends(connection: LiveFakeWebSocket, count: int) -> None:
    for _ in range(400):
        if len(connection.sent) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"expected {count} sends, got {len(connection.sent)}")


def _json(message: object) -> object:
    assert isinstance(message, TextMessage)
    return json.loads(message.data)


def test_graphql_protocol_envelopes_and_control_messages_are_explicit() -> None:
    protocol = GraphqlTransportWsProtocol()

    assert thaw_value(protocol.build_outbound("connection_init", freeze_value({}))) == {
        "type": "connection_init",
        "payload": {},
    }
    ack = protocol.inspect(frame_from_zapros(TextMessage('{"type":"connection_ack"}')))
    ping = protocol.inspect(frame_from_zapros(TextMessage('{"type":"ping","payload":{"nonce":1}}')))
    pong = protocol.build_control(ControlKind.PONG, freeze_value({"nonce": 1}))

    assert isinstance(ack, ParsedValue)
    assert ack.value.control is ControlKind.READY
    assert isinstance(ping, ParsedValue)
    assert ping.value.control is ControlKind.PING
    assert thaw_value(pong) == {"type": "pong", "payload": {"nonce": 1}}


async def test_connection_init_waits_for_ack_before_application_send() -> None:
    connection = LiveFakeWebSocket()
    client = AsyncWsClient(
        endpoint="wss://example.test/graphql",
        protocol=GraphqlTransportWsProtocol(),
        connector=FakeConnector([connection]),
        config=WsClientConfig(
            protocol_auth=ProtocolAuth(
                "connection_init",
                lambda: {"token": "fresh"},
                await_ready=True,
                ready_timeout=0.5,
            )
        ),
    )
    async with assert_no_task_leaks():
        application_send = asyncio.create_task(client.send("ping", {"nonce": "application"}))
        await _wait_for_sends(connection, 1)
        assert _json(connection.sent[0]) == {
            "type": "connection_init",
            "payload": {"token": "fresh"},
        }
        assert not application_send.done()

        connection.feed(TextMessage('{"type":"connection_ack"}'))
        await application_send
        assert len(connection.sent) == 2
        assert _json(connection.sent[1]) == {
            "type": "ping",
            "payload": {"nonce": "application"},
        }
        await client.aclose()


async def test_interleaved_graphql_next_routes_calls_and_subscriptions() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/graphql",
            protocol=GraphqlTransportWsProtocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        subscription = await client.subscribe(
            "subscribe",
            {"query": "subscription Prices { price }"},
            resubscribe=ResubscribeFromStart(),
        )
        query = asyncio.create_task(client.call("subscribe", {"query": "query Museum { open }"}))
        mutation = asyncio.create_task(
            client.call("subscribe", {"query": "mutation Buy { buy { id } }"})
        )
        await _wait_for_sends(connection, 3)
        assert _json(connection.sent[0])["id"] == "1"  # type: ignore[index]
        assert _json(connection.sent[1])["id"] == "2"  # type: ignore[index]
        assert _json(connection.sent[2])["id"] == "3"  # type: ignore[index]

        connection.feed(
            TextMessage('{"id":"3","type":"next","payload":{"data":{"buy":{"id":"b-1"}}}}')
        )
        connection.feed(TextMessage('{"id":"2","type":"next","payload":{"data":{"open":true}}}'))
        connection.feed(TextMessage('{"id":"1","type":"next","payload":{"data":{"price":42}}}'))
        assert await mutation == {"data": {"buy": {"id": "b-1"}}}
        assert await query == {"data": {"open": True}}
        assert (await anext(subscription)).value == {"data": {"price": 42}}

        connection.feed(TextMessage('{"id":"1","type":"complete"}'))
        with pytest.raises(StopAsyncIteration):
            await anext(subscription)


async def test_subscription_cancellation_sends_graphql_complete_through_writer() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/graphql",
            protocol=GraphqlTransportWsProtocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        subscription = await client.subscribe("subscribe", {"query": "subscription S { value }"})
        await subscription.aclose()
        await _wait_for_sends(connection, 2)
        assert _json(connection.sent[1]) == {"id": "1", "type": "complete"}


async def test_graphql_error_is_terminal_and_typed() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/graphql",
            protocol=GraphqlTransportWsProtocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        subscription = await client.subscribe("subscribe", {"query": "subscription Broken"})
        connection.feed(TextMessage('{"id":"1","type":"error","payload":[{"message":"denied"}]}'))
        with pytest.raises(GraphqlOperationError) as captured:
            await anext(subscription)
        assert captured.value.errors == [{"message": "denied"}]


async def test_graphql_fatal_close_and_duplicate_operation_code_do_not_reconnect() -> None:
    first = LiveFakeWebSocket()
    unused = LiveFakeWebSocket()
    connector = FakeConnector([first, unused])
    protocol = GraphqlTransportWsProtocol()
    client = AsyncWsClient(
        endpoint="wss://example.test/graphql",
        protocol=protocol,
        connector=connector,
        config=WsClientConfig(reconnect=ReconnectPolicy(delays=(0.0,))),
    )
    async with assert_no_task_leaks():
        subscription = await client.subscribe(
            "subscribe",
            {"query": "subscription S"},
            resubscribe=ResubscribeFromStart(),
        )
        first.feed(CloseMessage(4409, "Subscriber for 1 already exists"))
        with pytest.raises(SubscriptionDisconnectedError, match="fatal"):
            await anext(subscription)
        await asyncio.sleep(0)
        assert len(connector.attempts) == 1
        assert protocol.classify_close(4409) is CloseDisposition.FATAL


def test_graphql_next_error_and_complete_classification() -> None:
    protocol = GraphqlTransportWsProtocol()
    next_message = protocol.inspect(
        frame_from_zapros(TextMessage('{"id":"op","type":"next","payload":1}'))
    )
    complete = protocol.inspect(frame_from_zapros(TextMessage('{"id":"op","type":"complete"}')))
    error = protocol.inspect(
        frame_from_zapros(TextMessage('{"id":"op","type":"error","payload":[]}'))
    )

    assert isinstance(next_message, ParsedValue)
    assert next_message.value.kind is InboundMessageKind.REPLY
    assert isinstance(complete, ParsedValue)
    assert complete.value.control is ControlKind.COMPLETE
    assert isinstance(error, ParsedValue)
    assert isinstance(error.value.terminal_error, GraphqlOperationError)
