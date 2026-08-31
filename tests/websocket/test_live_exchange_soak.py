"""Opt-in public exchange WebSocket integration and soak tests.

These tests never authenticate or submit trading operations. Enable them explicitly with
``EAZY_SDK_RUN_LIVE_WS=1``; ordinary CI and local test runs remain network-independent.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from time import monotonic

import pytest

from eazy_sdk._internal.kernel import Malformed, ParseAttempt, ParsedValue
from eazy_sdk.websocket import (
    AsyncWsClient,
    ChannelKey,
    CloseDisposition,
    ControlKind,
    CorrelationKey,
    FrameKind,
    FrozenValue,
    InboundFrame,
    InboundMessageKind,
    JsonTextCodec,
    ProtocolEnvelopeError,
    ProtocolMessage,
    WsClientConfig,
    WsSessionState,
    freeze_value,
    thaw_value,
)
from tests.websocket._support import assert_no_task_leaks

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_network,
    pytest.mark.skipif(
        os.environ.get("EAZY_SDK_RUN_LIVE_WS") != "1",
        reason="set EAZY_SDK_RUN_LIVE_WS=1 to exercise public exchange WebSockets",
    ),
]

LIVE_SOAK_SECONDS = float(os.environ.get("EAZY_SDK_LIVE_WS_SOAK_SECONDS", "90"))


@dataclass(frozen=True, slots=True)
class ExchangeProtocol:
    """Small test-only adapter for the exchange's public JSON envelope."""

    operation_field: str
    discriminator_fields: tuple[str, ...]
    codec: JsonTextCodec = field(default_factory=JsonTextCodec)

    def build_outbound(
        self,
        discriminator: str,
        payload: FrozenValue,
        *,
        correlation: CorrelationKey | None = None,
        channel: ChannelKey | None = None,
    ) -> FrozenValue:
        if correlation is not None or channel is not None:
            raise ProtocolEnvelopeError("public exchange send does not use runtime routing keys")
        raw = thaw_value(payload)
        if not isinstance(raw, dict):
            raise ProtocolEnvelopeError("exchange payload must be an object")
        return freeze_value({self.operation_field: discriminator, **raw})

    def inspect(self, frame: InboundFrame) -> ParseAttempt[ProtocolMessage]:
        if frame.kind is FrameKind.PING:
            return ParsedValue(self._control(ControlKind.PING, frame.data))
        if frame.kind is FrameKind.PONG:
            return ParsedValue(self._control(ControlKind.PONG, frame.data))
        if frame.kind is FrameKind.CLOSE:
            return ParsedValue(self._control(ControlKind.CLOSE, frame.close_reason or ""))
        decoded = self.codec.decode(frame)
        if not isinstance(decoded, ParsedValue):
            return decoded
        raw = thaw_value(decoded.value)
        if not isinstance(raw, dict):
            return Malformed(ProtocolEnvelopeError("exchange envelope must be an object"))
        discriminator = self._discriminator(raw)
        return ParsedValue(
            ProtocolMessage(InboundMessageKind.MESSAGE, discriminator, decoded.value)
        )

    def classify_close(self, code: int | None) -> CloseDisposition:
        return CloseDisposition.NORMAL if code == 1000 else CloseDisposition.RECONNECT

    def build_recovery(self, channel: ChannelKey, token: FrozenValue) -> FrozenValue | None:
        return None

    def build_cancel(
        self,
        correlation: CorrelationKey | None,
        channel: ChannelKey | None,
    ) -> FrozenValue | None:
        return None

    def build_control(self, kind: ControlKind, payload: FrozenValue) -> FrozenValue | None:
        return None

    def _discriminator(self, raw: dict[object, object]) -> str | None:
        for name in self.discriminator_fields:
            value = raw.get(name)
            if isinstance(value, str):
                return value
        argument = raw.get("arg")
        if isinstance(argument, dict):
            channel = argument.get("channel")
            if isinstance(channel, str):
                return channel
        return None

    @staticmethod
    def _control(kind: ControlKind, payload: str | bytes) -> ProtocolMessage:
        value = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
        return ProtocolMessage(
            InboundMessageKind.CONTROL,
            None,
            freeze_value(value),
            control=kind,
        )


@dataclass(frozen=True, slots=True)
class ExchangeSpec:
    name: str
    endpoint: str
    protocol: ExchangeProtocol
    subscriptions: tuple[tuple[str, dict[str, object]], ...]
    data_discriminators: frozenset[str]


EXCHANGES = (
    ExchangeSpec(
        name="coinbase",
        endpoint="wss://advanced-trade-ws.coinbase.com",
        protocol=ExchangeProtocol("type", ("channel", "type")),
        subscriptions=(
            ("subscribe", {"channel": "heartbeats"}),
            ("subscribe", {"channel": "ticker", "product_ids": ["BTC-USD"]}),
        ),
        data_discriminators=frozenset({"heartbeats", "ticker"}),
    ),
    ExchangeSpec(
        name="okx",
        endpoint="wss://ws.okx.com:8443/ws/v5/public",
        protocol=ExchangeProtocol("op", ("event",)),
        subscriptions=(
            ("subscribe", {"args": [{"channel": "tickers", "instId": "BTC-USDT"}]}),
            ("subscribe", {"args": [{"channel": "tickers", "instId": "ETH-USDT"}]}),
        ),
        data_discriminators=frozenset({"tickers"}),
    ),
    ExchangeSpec(
        name="kraken-futures",
        endpoint="wss://futures.kraken.com/ws/v1",
        protocol=ExchangeProtocol("event", ("event", "feed")),
        subscriptions=(
            ("subscribe", {"feed": "ticker", "product_ids": ["PI_XBTUSD"]}),
            ("subscribe", {"feed": "ticker", "product_ids": ["PF_ETHUSD"]}),
        ),
        data_discriminators=frozenset({"ticker"}),
    ),
)


@dataclass(slots=True)
class StreamObservation:
    name: str
    received: int = 0
    data_messages: int = 0
    first_message_after: float | None = None
    close_seconds: float | None = None
    data_seen: asyncio.Event = field(default_factory=asyncio.Event)
    unsubscribe_seen: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: float = field(default_factory=monotonic)

    def observe(self, message: ProtocolMessage, expected: frozenset[str]) -> None:
        self.received += 1
        if self.first_message_after is None:
            self.first_message_after = monotonic() - self.started_at
        if message.discriminator in expected:
            self.data_messages += 1
            self.data_seen.set()
        if message.discriminator in {"subscriptions", "unsubscribe", "unsubscribed"}:
            self.unsubscribe_seen.set()


async def _exercise_exchange(
    spec: ExchangeSpec,
    *,
    hold_seconds: float = 0.0,
    unsubscribe: bool = False,
) -> StreamObservation:
    observation = StreamObservation(spec.name)
    client = AsyncWsClient(
        endpoint=spec.endpoint,
        protocol=spec.protocol,
        config=WsClientConfig(writer_queue_capacity=16, call_timeout=15.0),
        on_message=lambda message: observation.observe(message, spec.data_discriminators),
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=15.0)
        async with asyncio.TaskGroup() as sends:
            for discriminator, payload in spec.subscriptions:
                sends.create_task(client.send(discriminator, payload))
        await asyncio.wait_for(observation.data_seen.wait(), timeout=20.0)
        if hold_seconds:
            await asyncio.sleep(hold_seconds)
        assert client.state is WsSessionState.READY
        assert client.pending_count == 0
        if unsubscribe:
            observation.unsubscribe_seen.clear()
            async with asyncio.TaskGroup() as sends:
                for _, payload in spec.subscriptions:
                    sends.create_task(client.send("unsubscribe", payload))
            await asyncio.wait_for(observation.unsubscribe_seen.wait(), timeout=10.0)
    finally:
        started = monotonic()
        await asyncio.wait_for(client.aclose(), timeout=15.0)
        observation.close_seconds = monotonic() - started
        observation.closed.set()
    _assert_closed(client)
    assert observation.data_messages > 0
    assert observation.close_seconds is not None and observation.close_seconds < 15.0
    return observation


def _assert_closed(client: AsyncWsClient) -> None:
    assert client.state is WsSessionState.CLOSED


async def _cancel_during_stream(
    spec: ExchangeSpec,
    observation: StreamObservation,
) -> None:
    client = AsyncWsClient(
        endpoint=spec.endpoint,
        protocol=spec.protocol,
        config=WsClientConfig(writer_queue_capacity=16, call_timeout=15.0),
        on_message=lambda message: observation.observe(message, spec.data_discriminators),
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=15.0)
        async with asyncio.TaskGroup() as sends:
            for discriminator, payload in spec.subscriptions:
                sends.create_task(client.send(discriminator, payload))
        await asyncio.wait_for(observation.data_seen.wait(), timeout=20.0)
        await asyncio.Event().wait()
    finally:
        started = monotonic()
        await asyncio.wait_for(client.aclose(), timeout=15.0)
        observation.close_seconds = monotonic() - started
        _assert_closed(client)
        observation.closed.set()


def _report(observations: Sequence[StreamObservation]) -> None:
    print(
        "live WebSocket observations: "
        + "; ".join(
            f"{item.name}: messages={item.received}, data={item.data_messages}, "
            f"first={item.first_message_after:.3f}s, close={item.close_seconds:.3f}s"
            for item in observations
            if item.first_message_after is not None and item.close_seconds is not None
        )
    )


@pytest.mark.timeout(45)
@pytest.mark.parametrize("exchange", EXCHANGES, ids=lambda item: item.name)
async def test_public_exchange_stream_receives_data_and_closes(exchange: ExchangeSpec) -> None:
    async with assert_no_task_leaks():
        observation = await _exercise_exchange(exchange)
    _report((observation,))


@pytest.mark.timeout(60)
async def test_public_exchange_clients_and_writes_run_in_parallel() -> None:
    async with assert_no_task_leaks():
        observations = await asyncio.gather(*(_exercise_exchange(item) for item in EXCHANGES))
    assert {item.name for item in observations} == {item.name for item in EXCHANGES}
    _report(observations)


@pytest.mark.timeout(60)
async def test_public_exchange_unsubscribe_acknowledgements_and_close() -> None:
    async with assert_no_task_leaks():
        observations = await asyncio.gather(
            *(_exercise_exchange(item, unsubscribe=True) for item in EXCHANGES)
        )
    assert all(item.unsubscribe_seen.is_set() for item in observations)
    _report(observations)


@pytest.mark.timeout(60)
async def test_cancelling_parallel_stream_owners_closes_every_connection() -> None:
    observations = [StreamObservation(item.name) for item in EXCHANGES]
    async with assert_no_task_leaks():
        tasks = [
            asyncio.create_task(
                _cancel_during_stream(spec, observation),
                name=f"live-exchange-owner-{spec.name}",
            )
            for spec, observation in zip(EXCHANGES, observations, strict=True)
        ]
        await asyncio.wait_for(
            asyncio.gather(*(item.data_seen.wait() for item in observations)),
            timeout=30.0,
        )
        for task in tasks:
            task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(item, asyncio.CancelledError) for item in outcomes)
    assert all(item.closed.is_set() for item in observations)
    assert all(
        item.close_seconds is not None and item.close_seconds < 15.0 for item in observations
    )
    _report(observations)


@pytest.mark.timeout(180)
async def test_repeated_public_exchange_connection_churn_closes_cleanly() -> None:
    rounds = int(os.environ.get("EAZY_SDK_LIVE_WS_CHURN_ROUNDS", "3"))
    if not 2 <= rounds <= 10:
        pytest.fail("EAZY_SDK_LIVE_WS_CHURN_ROUNDS must be between 2 and 10")
    observations: list[StreamObservation] = []
    async with assert_no_task_leaks():
        for _ in range(rounds):
            observations.extend(
                await asyncio.gather(*(_exercise_exchange(item) for item in EXCHANGES))
            )
            await asyncio.sleep(0.5)
    assert len(observations) == rounds * len(EXCHANGES)
    assert all(item.closed.is_set() for item in observations)
    _report(observations)


@pytest.mark.slow
@pytest.mark.timeout(LIVE_SOAK_SECONDS + 60)
async def test_public_exchange_connections_survive_soak_and_close() -> None:
    if LIVE_SOAK_SECONDS < 30:
        pytest.fail("EAZY_SDK_LIVE_WS_SOAK_SECONDS must be at least 30")
    async with assert_no_task_leaks():
        observations = await asyncio.gather(
            *(_exercise_exchange(item, hold_seconds=LIVE_SOAK_SECONDS) for item in EXCHANGES)
        )
    assert all(item.data_messages >= 2 for item in observations)
    _report(observations)
