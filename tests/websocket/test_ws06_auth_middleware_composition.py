from __future__ import annotations

import ast
import asyncio
import inspect
import json
from collections.abc import Callable

import pytest
from zapros.websocket import TextMessage

import eazy_sdk.websocket.middleware as websocket_middleware
from eazy_sdk.websocket import (
    AsyncWsClient,
    ConnectionMiddlewareApplication,
    DynamicPerMessageAuth,
    HmacSha256MessageSignature,
    JsonEventProtocol,
    MessageMiddlewareApplication,
    ProtocolAuth,
    ReconnectPolicy,
    ResubscribeFromStart,
    RuntimeComposition,
    SecretBytes,
    SecretText,
    StaticUpgradeAuth,
    SubscriptionMiddlewareApplication,
    WsClientConfig,
    WsContinue,
    WsDirection,
    WsMessagePatch,
    WsMiddlewareContext,
    WsOutput,
    WsReject,
    WsScope,
)
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks


def _protocol() -> JsonEventProtocol:
    return JsonEventProtocol(
        event_field="type",
        payload_field="data",
        correlation_field="id",
        channel_field="topic",
    )


async def _wait_until(predicate: Callable[[], bool], message: str) -> None:
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(message)


def _text_data(message: object) -> str:
    assert isinstance(message, TextMessage)
    return message.data


async def test_protocol_auth_is_refreshed_and_serialized_before_resubscribe() -> None:
    first = LiveFakeWebSocket()
    second = LiveFakeWebSocket()
    credentials = iter(("credential-1", "credential-2"))
    calls: list[str] = []
    connection_contexts: list[WsMiddlewareContext] = []
    subscription_events: list[str | None] = []

    async def credential() -> object:
        value = next(credentials)
        calls.append(value)
        return {"credential": value}

    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([first, second]),
        config=WsClientConfig(
            reconnect=ReconnectPolicy(delays=(0.0,)),
            protocol_auth=ProtocolAuth("authenticate", credential),
            connection_middleware=(
                ConnectionMiddlewareApplication(_ConnectionObserver(connection_contexts)),
            ),
            subscription_middleware=(
                SubscriptionMiddlewareApplication(_SubscriptionObserver(subscription_events)),
            ),
        ),
    )
    async with assert_no_task_leaks():
        subscription = await client.subscribe(
            "subscribe",
            {},
            channel="prices",
            resubscribe=ResubscribeFromStart(),
        )
        await _wait_until(lambda: len(first.sent) == 2, "initial auth/subscription not sent")
        first.feed(ConnectionError("connection reset"))
        await _wait_until(lambda: len(second.sent) == 2, "reconnect auth/subscription not sent")

        assert calls == ["credential-1", "credential-2"]
        assert [json.loads(_text_data(message))["type"] for message in first.sent] == [
            "authenticate",
            "subscribe",
        ]
        assert [json.loads(_text_data(message))["type"] for message in second.sent] == [
            "authenticate",
            "subscribe",
        ]
        assert json.loads(_text_data(second.sent[0]))["data"] == {"credential": "credential-2"}
        assert [context.event for context in connection_contexts] == ["connect", "connect"]
        assert subscription_events == ["subscribe", "resubscribe"]
        await subscription.aclose()
        await client.aclose()


async def test_dynamic_per_message_auth_is_refreshed_for_every_attempt() -> None:
    connection = LiveFakeWebSocket()
    credentials = iter(("message-1", "message-2"))
    calls = 0

    async def credential() -> SecretText:
        nonlocal calls
        calls += 1
        return SecretText(next(credentials))

    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([connection]),
        config=WsClientConfig(
            dynamic_per_message_auth=(DynamicPerMessageAuth(("authorization",), credential),)
        ),
    )
    async with assert_no_task_leaks():
        await client.send("first", {})
        await client.send("second", {})
        await client.aclose()

    assert calls == 2
    assert [json.loads(_text_data(message))["authorization"] for message in connection.sent] == [
        "message-1",
        "message-2",
    ]
    assert "message-1" not in repr(client.config)


def test_static_upgrade_auth_is_immutable_and_redacted() -> None:
    auth = StaticUpgradeAuth(
        {
            "authorization": SecretText("Bearer static-secret"),
            "x-client": "eazy_sdk",
        }
    )

    assert auth.headers() == {
        "authorization": "Bearer static-secret",
        "x-client": "eazy_sdk",
    }
    assert "static-secret" not in repr(auth)
    with pytest.raises(TypeError):
        auth.headers()["new"] = "value"  # type: ignore[index]


class _ConnectionObserver:
    def __init__(self, seen: list[WsMiddlewareContext]) -> None:
        self.seen = seen

    async def __call__(self, context: WsMiddlewareContext) -> WsContinue:
        self.seen.append(context)
        return WsContinue()


class _MessageTenant:
    async def __call__(self, context: WsMiddlewareContext) -> WsMessagePatch:
        assert context.direction is WsDirection.OUTBOUND
        return WsMessagePatch((WsOutput(("tenant",), "alpha"),))


class _SubscriptionObserver:
    def __init__(self, events: list[str | None]) -> None:
        self.events = events

    def __call__(self, context: WsMiddlewareContext) -> WsContinue:
        self.events.append(context.event)
        return WsContinue()


async def test_scoped_middleware_has_explicit_lifetimes_and_typed_outputs() -> None:
    connection = LiveFakeWebSocket()
    connection_contexts: list[WsMiddlewareContext] = []
    subscription_events: list[str | None] = []
    config = WsClientConfig(
        writer_queue_capacity=1,
        outbound_message_transforms=(
            HmacSha256MessageSignature(key=SecretBytes(b"middleware-test-key")),
        ),
        connection_middleware=(
            ConnectionMiddlewareApplication(
                _ConnectionObserver(connection_contexts),
                WsScope(endpoints=frozenset({"wss://example.test/ws"})),
            ),
        ),
        message_middleware=(
            MessageMiddlewareApplication(
                _MessageTenant(),
                WsScope(
                    operations=frozenset({"publish"}),
                    directions=frozenset({WsDirection.OUTBOUND}),
                ),
            ),
        ),
        subscription_middleware=(
            SubscriptionMiddlewareApplication(_SubscriptionObserver(subscription_events)),
        ),
    )
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([connection]),
        config=config,
    )
    async with assert_no_task_leaks():
        await client.send("publish", {"value": 1})
        subscription = await client.subscribe("subscribe", {}, channel="prices")
        connection.feed(TextMessage('{"type":"tick","topic":"prices","data":{"value":2}}'))
        assert (await anext(subscription)).value == {"value": 2}
        await subscription.aclose()
        await client.aclose()

    assert len(connection_contexts) == 1
    assert connection_contexts[0].event == "connect"
    sent = json.loads(_text_data(connection.sent[0]))
    assert sent["tenant"] == "alpha"
    assert isinstance(sent["signature"], str)
    assert client.last_protection_snapshot is not None
    assert client.last_protection_snapshot.semantic_transforms == ("hmac-sha256-message",)
    assert subscription_events == ["subscribe", "tick"]


class _RejectMessage:
    def __call__(self, context: WsMiddlewareContext) -> WsReject:
        return WsReject(PermissionError(f"blocked {context.operation}"))


async def test_middleware_rejection_happens_before_writer_and_protection() -> None:
    connection = LiveFakeWebSocket()
    client = AsyncWsClient(
        endpoint="wss://example.test/ws",
        protocol=_protocol(),
        connector=FakeConnector([connection]),
        config=WsClientConfig(
            message_middleware=(
                MessageMiddlewareApplication(
                    _RejectMessage(),
                    WsScope(operations=frozenset({"blocked"})),
                ),
            )
        ),
    )
    async with assert_no_task_leaks():
        with pytest.raises(PermissionError, match="blocked blocked"):
            await client.send("blocked", {})
        assert connection.sent == []
        assert client.last_protection_snapshot is None
        await client.aclose()


async def test_composition_keeps_http_and_ws_lifecycles_separate_and_bootstraps_endpoint() -> None:
    http_runtime = object()
    created: list[str] = []

    def make_websocket(endpoint: str, models: object) -> AsyncWsClient:
        created.append(endpoint)
        return AsyncWsClient(
            endpoint=endpoint,
            protocol=_protocol(),
            connector=FakeConnector([LiveFakeWebSocket()]),
            config=WsClientConfig(models=models),  # type: ignore[arg-type]
        )

    composition = RuntimeComposition(http=http_runtime, websocket_factory=make_websocket)
    assert composition.http is http_runtime
    assert created == []

    bootstrap_calls = 0

    async def public_http_operation() -> str:
        nonlocal bootstrap_calls
        bootstrap_calls += 1
        return "wss://bootstrap.test/socket"

    websocket = await composition.bootstrap_websocket(public_http_operation)
    assert bootstrap_calls == 1
    assert created == ["wss://bootstrap.test/socket"]
    assert composition.websocket("wss://bootstrap.test/socket") is websocket
    await websocket.aclose()


def test_ws_scope_matches_all_declared_dimensions() -> None:
    scope = WsScope(
        operations=frozenset({"publish"}),
        endpoints=frozenset({"wss://example.test/ws"}),
        protocols=frozenset({"JsonEventProtocol"}),
        channels=frozenset({"prices"}),
        events=frozenset({"tick"}),
        directions=frozenset({WsDirection.INBOUND}),
    )
    context = WsMiddlewareContext(
        operation="publish",
        endpoint="wss://example.test/ws",
        protocol="JsonEventProtocol",
        channel="prices",
        event="tick",
        direction=WsDirection.INBOUND,
        generation=1,
    )

    assert scope.matches(context)
    assert not scope.matches(
        WsMiddlewareContext(
            operation="publish",
            endpoint="wss://example.test/ws",
            protocol="JsonEventProtocol",
            channel="orders",
            event="tick",
            direction=WsDirection.INBOUND,
            generation=1,
        )
    )


def test_middleware_contract_exposes_no_runtime_io_capability() -> None:
    context_fields = set(WsMiddlewareContext.__dataclass_fields__)
    assert context_fields.isdisjoint(
        {"client", "websocket", "writer", "reader", "reconnect", "queue"}
    )

    tree = ast.parse(inspect.getsource(websocket_middleware))
    forbidden = {"connect", "send", "recv", "reconnect"}
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    assert called.isdisjoint(forbidden)
