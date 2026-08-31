from __future__ import annotations

import asyncio

import pytest
from zapros.websocket import CloseMessage, TextMessage

from eazy_sdk.websocket import (
    AsyncWsApi,
    AsyncWsClient,
    DeliveryNotSentError,
    DeliveryUnknownError,
    JsonEventProtocol,
    NeverReplay,
    ReplayIfUnsent,
    ReplayWithDeduplication,
    WsCallTimeoutError,
    WsClientConfig,
    WsQueueOverflowError,
    WsSessionState,
    ws,
)
from tests.websocket._support import (
    FakeConnector,
    LiveFakeWebSocket,
    assert_no_task_leaks,
)


def _protocol() -> JsonEventProtocol:
    return JsonEventProtocol(
        event_field="type",
        payload_field="data",
        correlation_field="id",
    )


async def _wait_for_sends(connection: LiveFakeWebSocket, count: int) -> None:
    for _ in range(100):
        if len(connection.sent) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"expected {count} sends, got {len(connection.sent)}")


async def test_parallel_calls_route_out_of_order_replies_to_their_generation_keys() -> None:
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
        first = asyncio.create_task(client.call("lookup", {"name": "first"}))
        second = asyncio.create_task(client.call("lookup", {"name": "second"}))
        await _wait_for_sends(connection, 2)

        ids = [message.data for message in connection.sent if isinstance(message, TextMessage)]
        assert ids == [
            '{"data":{"name":"first"},"id":"1","type":"lookup"}',
            '{"data":{"name":"second"},"id":"2","type":"lookup"}',
        ]

        connection.feed(TextMessage('{"type":"result","id":"2","data":"SECOND"}'))
        connection.feed(TextMessage('{"type":"result","id":"1","data":"FIRST"}'))

        assert await first == "FIRST"
        assert await second == "SECOND"
        assert client.pending_count == 0
        assert client.state is WsSessionState.READY


async def test_send_failure_after_zapros_send_begins_is_delivery_unknown() -> None:
    connection = LiveFakeWebSocket(send_failures=[RuntimeError("socket reset")])
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([connection]),
    )

    async with assert_no_task_leaks():
        with pytest.raises(DeliveryUnknownError, match="may have been delivered"):
            await client.send("notify", {"value": 1}, replay=NeverReplay())
        await client.aclose()

    assert client.pending_count == 0
    assert len(connection.sent) == 1


async def test_explicit_deduplication_policy_rebuilds_on_a_fresh_generation() -> None:
    first_connection = LiveFakeWebSocket(send_failures=[RuntimeError("socket reset")])
    second_connection = LiveFakeWebSocket()
    connector = FakeConnector([first_connection, second_connection])

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=connector,
        ) as client,
    ):
        call = asyncio.create_task(
            client.call(
                "create",
                {"deduplication_key": "order-1"},
                replay=ReplayWithDeduplication("order-1", max_replays=1),
            )
        )
        await _wait_for_sends(second_connection, 1)
        second_connection.feed(TextMessage('{"type":"created","id":"2","data":{"ok":true}}'))

        assert await call == {"ok": True}
        assert client.generation.value == 2
        assert len(connector.attempts) == 2
        assert first_connection.sent[0] != second_connection.sent[0]


async def test_disconnect_fails_pending_and_late_old_generation_reply_is_ignored() -> None:
    first_connection = LiveFakeWebSocket()
    second_connection = LiveFakeWebSocket()
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([first_connection, second_connection]),
    )

    async with assert_no_task_leaks():
        first_call = asyncio.create_task(client.call("lookup", {"value": 1}))
        await _wait_for_sends(first_connection, 1)
        first_connection.feed(CloseMessage(1012, "restart"))
        with pytest.raises(DeliveryUnknownError):
            await first_call

        second_call = asyncio.create_task(client.call("lookup", {"value": 2}))
        await _wait_for_sends(second_connection, 1)
        first_connection.feed(TextMessage('{"type":"result","id":"2","data":"STALE"}'))
        await asyncio.sleep(0)
        assert not second_call.done()

        second_connection.feed(TextMessage('{"type":"result","id":"2","data":"CURRENT"}'))
        assert await second_call == "CURRENT"
        await client.aclose()

    assert client.state is WsSessionState.CLOSED


async def test_replay_if_unsent_rebuilds_only_the_queued_exchange() -> None:
    send_gate = asyncio.Event()
    first_connection = LiveFakeWebSocket(send_gate=send_gate)
    second_connection = LiveFakeWebSocket()
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([first_connection, second_connection]),
    )

    async with assert_no_task_leaks():
        uncertain = asyncio.create_task(client.send("notify", {"value": 1}))
        await first_connection.send_started.wait()
        replayed = asyncio.create_task(
            client.call(
                "lookup",
                {"value": 2},
                replay=ReplayIfUnsent(max_replays=1),
            )
        )
        await asyncio.sleep(0)
        first_connection.feed(CloseMessage(1012, "restart"))

        with pytest.raises(DeliveryUnknownError):
            await uncertain
        await _wait_for_sends(second_connection, 1)
        second_connection.feed(TextMessage('{"type":"result","id":"2","data":"replayed"}'))
        assert await replayed == "replayed"
        await client.aclose()

    assert first_connection.sent == []
    assert second_connection.sent == [TextMessage('{"data":{"value":2},"id":"2","type":"lookup"}')]


async def test_bounded_writer_queue_fails_closed_and_decorator_uses_same_runtime() -> None:
    connection = LiveFakeWebSocket()
    connector = FakeConnector([connection])

    class Notifications(AsyncWsApi):
        @ws.send("notify")
        async def notify(self, value: int) -> None:
            raise NotImplementedError

        @ws.call("lookup")
        async def lookup(self, name: str) -> str:
            raise NotImplementedError

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=connector,
            config=WsClientConfig(writer_queue_capacity=1),
        ) as client,
    ):
        api = Notifications(client)
        await api.notify(3)
        call = asyncio.create_task(api.lookup("museum"))
        await _wait_for_sends(connection, 2)
        connection.feed(TextMessage('{"type":"result","id":"1","data":"open"}'))
        assert await call == "open"

    assert connection.sent == [
        TextMessage('{"data":{"value":3},"type":"notify"}'),
        TextMessage('{"data":{"name":"museum"},"id":"1","type":"lookup"}'),
    ]


async def test_writer_queue_overflow_fails_without_starting_an_extra_send() -> None:
    send_gate = asyncio.Event()
    connection = LiveFakeWebSocket(send_gate=send_gate)
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([connection]),
        config=WsClientConfig(writer_queue_capacity=1),
    )

    async with assert_no_task_leaks():
        first = asyncio.create_task(client.send("notify", {"value": 1}))
        await connection.send_started.wait()
        second = asyncio.create_task(client.send("notify", {"value": 2}))
        await asyncio.sleep(0)
        with pytest.raises(WsQueueOverflowError, match="capacity 1"):
            await client.send("notify", {"value": 3})

        send_gate.set()
        await first
        await second
        await client.aclose()

    assert len(connection.sent) == 2


async def test_cancellation_and_timeout_remove_pending_once() -> None:
    connection = LiveFakeWebSocket()
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([connection]),
        config=WsClientConfig(call_timeout=None),
    )

    async with assert_no_task_leaks():
        cancelled = asyncio.create_task(client.call("lookup", {"value": 1}))
        await _wait_for_sends(connection, 1)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert client.pending_count == 0

        with pytest.raises(WsCallTimeoutError, match="timed out"):
            await client.call("lookup", {"value": 2}, timeout=0.001)
        assert client.pending_count == 0
        await client.aclose()


async def test_user_message_handler_failure_does_not_stop_reader() -> None:
    connection = LiveFakeWebSocket()
    handled = asyncio.Event()

    async def broken_handler(message: object) -> None:
        handled.set()
        raise RuntimeError(f"handler rejected {message!r}")

    async with (
        assert_no_task_leaks(),
        AsyncWsClient(
            endpoint="wss://example.test/ws",
            protocol=_protocol(),
            connector=FakeConnector([connection]),
            on_message=broken_handler,
        ) as client,
    ):
        await client.connect()
        connection.feed(TextMessage('{"type":"notice","data":"hello"}'))
        await handled.wait()

        call = asyncio.create_task(client.call("lookup", {"value": 1}))
        await _wait_for_sends(connection, 1)
        connection.feed(TextMessage('{"type":"result","id":"1","data":"ok"}'))
        assert await call == "ok"


async def test_graceful_close_finishes_active_and_queued_operations_without_leaks() -> None:
    send_gate = asyncio.Event()
    connection = LiveFakeWebSocket(send_gate=send_gate)
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([connection]),
        config=WsClientConfig(writer_queue_capacity=2),
    )

    async with assert_no_task_leaks():
        active = asyncio.create_task(client.send("notify", {"value": 1}))
        await connection.send_started.wait()
        queued = asyncio.create_task(client.call("lookup", {"value": 2}))
        await asyncio.sleep(0)

        await client.aclose()
        with pytest.raises(DeliveryUnknownError):
            await active
        with pytest.raises(DeliveryNotSentError):
            await queued

    assert client.pending_count == 0
    assert client.state is WsSessionState.CLOSED
