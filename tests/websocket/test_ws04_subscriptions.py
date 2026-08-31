from __future__ import annotations

import asyncio

import pytest
from zapros.websocket import CloseMessage, TextMessage

from eazy_sdk.websocket import (
    AsyncWsApi,
    AsyncWsClient,
    ControlEvent,
    ControlKind,
    DeliveryUnknownError,
    Event,
    JsonEventProtocol,
    OverflowPolicy,
    ReconnectPolicy,
    RecoverBySequence,
    RecoverByToken,
    RecoveryGapError,
    ResubscribeFromStart,
    Subscription,
    SubscriptionDisconnectedError,
    SubscriptionOverflowError,
    SubscriptionState,
    WsClientConfig,
    WsSessionState,
    ws,
)
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks


def _protocol() -> JsonEventProtocol:
    return JsonEventProtocol(
        event_field="type",
        payload_field="data",
        correlation_field="id",
        channel_field="topic",
        recovery_event="recover",
        recovery_token_field="offset",
        controls=(ControlEvent("complete", ControlKind.COMPLETE),),
        fatal_close_codes=frozenset({4401}),
        reconnect_close_codes=frozenset({1012}),
    )


async def _wait_for_sends(connection: LiveFakeWebSocket, count: int) -> None:
    for _ in range(200):
        if len(connection.sent) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"expected {count} sends, got {len(connection.sent)}")


async def _wait_for_attempts(connector: FakeConnector, count: int) -> None:
    for _ in range(200):
        if len(connector.attempts) >= count:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"expected {count} connection attempts, got {len(connector.attempts)}")


async def test_slow_subscription_consumer_does_not_block_call_reply_routing() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        subscription = await client.subscribe(
            "subscribe",
            {"symbol": "BTCUSDT"},
            channel="prices",
        )
        connection.feed(TextMessage('{"type":"tick","topic":"prices","data":1}'))
        connection.feed(TextMessage('{"type":"tick","topic":"prices","data":2}'))

        call = asyncio.create_task(client.call("lookup", {"name": "museum"}))
        await _wait_for_sends(connection, 2)
        connection.feed(TextMessage('{"type":"result","id":"1","data":"open"}'))
        assert await call == "open"

        assert await anext(subscription) == Event(1, client.generation, recovered=False)
        assert await anext(subscription) == Event(2, client.generation, recovered=False)
        await subscription.aclose()
        await subscription.aclose()


async def test_transient_disconnect_fails_call_but_resubscribes_from_start() -> None:
    first = LiveFakeWebSocket()
    second = LiveFakeWebSocket()
    connector = FakeConnector([first, second])
    config = WsClientConfig(reconnect=ReconnectPolicy(delays=(0.0,)))

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=connector,
            config=config,
        ) as client,
    ):
        subscription = await client.subscribe(
            "subscribe",
            {"symbol": "BTCUSDT"},
            channel="prices",
            resubscribe=ResubscribeFromStart(),
        )
        pending_call = asyncio.create_task(client.call("lookup", {"value": 1}))
        await _wait_for_sends(first, 2)
        first.feed(CloseMessage(1012, "restart"))

        with pytest.raises(DeliveryUnknownError):
            await pending_call
        await _wait_for_attempts(connector, 2)
        await _wait_for_sends(second, 1)
        assert len(second.sent) == 1

        second.feed(TextMessage('{"type":"tick","topic":"prices","data":7}'))
        event = await anext(subscription)
        assert event == Event(7, client.generation, recovered=False)
        assert event.generation.value == 2


async def test_sequence_recovery_uses_recovery_frame_and_reports_gap() -> None:
    first = LiveFakeWebSocket()
    second = LiveFakeWebSocket()
    connector = FakeConnector([first, second])

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=connector,
            config=WsClientConfig(reconnect=ReconnectPolicy(delays=(0.0,))),
        ) as client,
    ):
        subscription = await client.subscribe(
            "subscribe",
            {"symbol": "BTCUSDT"},
            channel="prices",
            resubscribe=RecoverBySequence("sequence"),
        )
        first.feed(TextMessage('{"type":"tick","topic":"prices","data":{"sequence":1,"price":10}}'))
        first_event = await anext(subscription)
        assert first_event.last_position == 1
        assert first_event.recovered is False

        first.feed(CloseMessage(1012, "restart"))
        await _wait_for_sends(second, 1)
        assert second.sent == [
            TextMessage('{"data":{"offset":1},"topic":"prices","type":"recover"}')
        ]

        second.feed(
            TextMessage('{"type":"tick","topic":"prices","data":{"sequence":3,"price":11}}')
        )
        with pytest.raises(RecoveryGapError, match="expected sequence 2, received 3"):
            await anext(subscription)


async def test_token_recovery_and_drop_oldest_overflow_are_explicit() -> None:
    first = LiveFakeWebSocket()
    second = LiveFakeWebSocket()
    connector = FakeConnector([first, second])

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=connector,
            config=WsClientConfig(reconnect=ReconnectPolicy(delays=(0.0,))),
        ) as client,
    ):
        subscription = await client.subscribe(
            "subscribe",
            {},
            channel="prices",
            resubscribe=RecoverByToken("cursor"),
            queue_capacity=1,
            overflow=OverflowPolicy.DROP_OLDEST,
        )
        first.feed(TextMessage('{"type":"tick","topic":"prices","data":{"cursor":"a","price":1}}'))
        first.feed(TextMessage('{"type":"tick","topic":"prices","data":{"cursor":"b","price":2}}'))
        latest = await anext(subscription)
        assert latest.value == {"cursor": "b", "price": 2}
        assert latest.last_position == "b"
        assert latest.gap is True

        first.feed(CloseMessage(1012, "restart"))
        await _wait_for_sends(second, 1)
        assert second.sent == [
            TextMessage('{"data":{"offset":"b"},"topic":"prices","type":"recover"}')
        ]
        second.feed(TextMessage('{"type":"tick","topic":"prices","data":{"cursor":"c","price":3}}'))
        recovered = await anext(subscription)
        assert recovered.recovered is True
        assert recovered.generation.value == 2


async def test_protocol_completion_finishes_subscription_iterator() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        subscription = await client.subscribe("subscribe", {}, channel="prices")
        connection.feed(TextMessage('{"type":"complete","topic":"prices","data":{}}'))
        with pytest.raises(StopAsyncIteration):
            await anext(subscription)
        assert subscription.state is SubscriptionState.COMPLETED


async def test_fatal_close_does_not_start_reconnect_loop() -> None:
    first = LiveFakeWebSocket()
    unused = LiveFakeWebSocket()
    connector = FakeConnector([first, unused])
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=connector,
        config=WsClientConfig(reconnect=ReconnectPolicy(delays=(0.0, 0.0))),
    )

    async with assert_no_task_leaks():
        subscription = await client.subscribe(
            "subscribe",
            {},
            channel="prices",
            resubscribe=ResubscribeFromStart(),
        )
        first.feed(CloseMessage(4401, "unauthorized"))
        with pytest.raises(SubscriptionDisconnectedError, match="fatal"):
            await anext(subscription)
        await asyncio.sleep(0)
        assert len(connector.attempts) == 1
        await client.aclose()


async def test_subscription_overflow_fails_without_blocking_reader() -> None:
    connection = LiveFakeWebSocket()
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        subscription = await client.subscribe(
            "subscribe",
            {},
            channel="prices",
            queue_capacity=1,
            overflow=OverflowPolicy.FAIL,
        )
        healthy = await client.subscribe("subscribe", {}, channel="news")
        connection.feed(TextMessage('{"type":"tick","topic":"prices","data":1}'))
        connection.feed(TextMessage('{"type":"tick","topic":"prices","data":2}'))
        connection.feed(TextMessage('{"type":"headline","topic":"news","data":"ok"}'))
        with pytest.raises(SubscriptionOverflowError, match="capacity 1"):
            await anext(subscription)
        assert (await anext(healthy)).value == "ok"


async def test_reconnect_budget_exhaustion_terminates_retained_subscription() -> None:
    first = LiveFakeWebSocket()
    connector = FakeConnector([first, RuntimeError("dial one"), RuntimeError("dial two")])

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=connector,
            config=WsClientConfig(reconnect=ReconnectPolicy(delays=(0.0, 0.0))),
        ) as client,
    ):
        subscription = await client.subscribe(
            "subscribe",
            {},
            channel="prices",
            resubscribe=ResubscribeFromStart(),
        )
        first.feed(CloseMessage(1012, "restart"))
        with pytest.raises(SubscriptionDisconnectedError, match="budget exhausted"):
            await anext(subscription)
        assert len(connector.attempts) == 3
        assert client.state is WsSessionState.FAILED


async def test_missing_heartbeat_ack_uses_bounded_reconnect_budget() -> None:
    first = LiveFakeWebSocket()
    second = LiveFakeWebSocket()
    connector = FakeConnector([first, second])
    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=connector,
            config=WsClientConfig(
                reconnect=ReconnectPolicy(delays=(0.0,)),
                heartbeat_interval=0.001,
                heartbeat_timeout=0.001,
            ),
        ) as client,
    ):
        await client.subscribe(
            "subscribe",
            {},
            channel="prices",
            resubscribe=ResubscribeFromStart(),
        )
        await _wait_for_attempts(connector, 2)
        assert len(connector.attempts) == 2


async def test_ws_subscribe_decorator_uses_the_session_registry() -> None:
    connection = LiveFakeWebSocket()

    class Prices(AsyncWsApi):
        @ws.subscribe("subscribe", resubscribe=ResubscribeFromStart())
        async def stream(self, symbol: str) -> Subscription[int]:
            raise NotImplementedError

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
        ) as client,
    ):
        subscription = await Prices(client).stream("BTCUSDT")
        assert subscription.state.value == "active"
        assert connection.sent == [
            TextMessage('{"data":{"symbol":"BTCUSDT"},"id":"1","type":"subscribe"}')
        ]
        connection.feed(TextMessage('{"type":"tick","id":"1","data":42}'))
        assert (await anext(subscription)).value == 42
