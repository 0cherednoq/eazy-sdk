"""Scripted in-memory implementation of the Zapros WebSocket boundary."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from zapros.websocket import AsyncBaseWebSocket, CloseCode, CloseMessage, Message


class ScriptExhausted(AssertionError):
    """Raised when a fake receives more messages than its script contains."""


@dataclass(frozen=True, slots=True)
class ConnectAttempt:
    """One call made through :class:`FakeConnector`."""

    url: str
    client: object | None
    subprotocols: tuple[str, ...]
    permessage_deflate: object


class FakeWebSocket(AsyncBaseWebSocket):
    """A network-free, scripted ``AsyncBaseWebSocket`` implementation."""

    def __init__(self, incoming: Iterable[Message | BaseException] = ()) -> None:
        self._incoming = deque(incoming)
        self.sent: list[Message] = []
        self.close_calls: list[tuple[int, str]] = []
        self._close_code: int | None = None
        self._close_reason: str | None = None

    async def send(self, message: Message) -> None:
        if self._close_code is not None:
            raise RuntimeError("cannot send on a closed fake WebSocket")
        self.sent.append(message)

    async def recv(self) -> Message:
        if not self._incoming:
            raise ScriptExhausted("fake WebSocket receive script is exhausted")
        item = self._incoming.popleft()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, CloseMessage):
            self._close_code = int(item.code)
            self._close_reason = item.reason or ""
        return item

    async def close(self, code: int = CloseCode.NORMAL, reason: str = "") -> None:
        if self._close_code is not None:
            return
        self._close_code = int(code)
        self._close_reason = reason
        self.close_calls.append((int(code), reason))

    def __aiter__(self) -> AsyncIterator[Message]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Message]:
        while self._incoming:
            message = await self.recv()
            yield message
            if isinstance(message, CloseMessage):
                return

    @property
    def close_code(self) -> int | None:
        return self._close_code

    @property
    def close_reason(self) -> str | None:
        return self._close_reason


class LiveFakeWebSocket(AsyncBaseWebSocket):
    """Controllable connection whose inbound frames can be interleaved with sends."""

    def __init__(
        self,
        *,
        send_failures: Iterable[BaseException | None] = (),
        send_gate: asyncio.Event | None = None,
    ) -> None:
        self._incoming: asyncio.Queue[Message | BaseException] = asyncio.Queue()
        self._send_failures = deque(send_failures)
        self._send_gate = send_gate
        self.send_started = asyncio.Event()
        self.sent: list[Message] = []
        self.close_calls: list[tuple[int, str]] = []
        self._close_code: int | None = None
        self._close_reason: str | None = None

    async def send(self, message: Message) -> None:
        if self._close_code is not None:
            raise RuntimeError("cannot send on a closed live fake WebSocket")
        self.send_started.set()
        if self._send_gate is not None:
            await self._send_gate.wait()
        self.sent.append(message)
        if self._send_failures:
            failure = self._send_failures.popleft()
            if failure is not None:
                raise failure

    async def recv(self) -> Message:
        item = await self._incoming.get()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, CloseMessage):
            self._close_code = int(item.code)
            self._close_reason = item.reason or ""
        return item

    def feed(self, item: Message | BaseException) -> None:
        self._incoming.put_nowait(item)

    async def close(self, code: int = CloseCode.NORMAL, reason: str = "") -> None:
        if self._close_code is not None:
            return
        self._close_code = int(code)
        self._close_reason = reason
        self.close_calls.append((int(code), reason))

    def __aiter__(self) -> AsyncIterator[Message]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Message]:
        while True:
            message = await self.recv()
            yield message
            if isinstance(message, CloseMessage):
                return

    @property
    def close_code(self) -> int | None:
        return self._close_code

    @property
    def close_reason(self) -> str | None:
        return self._close_reason


class _FakeConnectionContext:
    def __init__(self, connector: FakeConnector) -> None:
        self._connector = connector
        self._websocket: FakeWebSocket | LiveFakeWebSocket | None = None

    async def __aenter__(self) -> FakeWebSocket | LiveFakeWebSocket:
        self._websocket = self._connector._next_connection()
        return self._websocket

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._websocket is not None:
            await self._websocket.close()


class FakeConnector:
    """Callable fake with the public call shape of ``zapros.websocket.aconnect_ws``."""

    def __init__(
        self,
        connections: Iterable[FakeWebSocket | LiveFakeWebSocket | BaseException],
    ) -> None:
        self._connections = deque(connections)
        self.attempts: list[ConnectAttempt] = []

    def __call__(
        self,
        url: object,
        *,
        client: object | None = None,
        subprotocols: list[str] | None = None,
        permessage_deflate: object = False,
    ) -> _FakeConnectionContext:
        self.attempts.append(
            ConnectAttempt(
                url=str(url),
                client=client,
                subprotocols=tuple(subprotocols or ()),
                permessage_deflate=permessage_deflate,
            )
        )
        return _FakeConnectionContext(self)

    def _next_connection(self) -> FakeWebSocket | LiveFakeWebSocket:
        if not self._connections:
            raise ScriptExhausted("fake connector script is exhausted")
        item = self._connections.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


@dataclass(slots=True)
class DeterministicClock:
    """Monotonic test clock advanced only by its injected sleep function."""

    value: float = 0.0

    def now(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        if delay < 0:
            raise ValueError("delay cannot be negative")
        self.value += delay
        await asyncio.sleep(0)


class ScriptedBackoff:
    """Finite deterministic reconnect schedule backed by a test clock."""

    def __init__(self, delays: Iterable[float], *, clock: DeterministicClock) -> None:
        self._delays: Iterator[float] = iter(delays)
        self.clock = clock
        self.waits: list[tuple[int, float]] = []

    async def wait(self, attempt: int) -> float:
        try:
            delay = next(self._delays)
        except StopIteration as exc:
            raise ScriptExhausted("fake backoff script is exhausted") from exc
        self.waits.append((attempt, delay))
        await self.clock.sleep(delay)
        return delay


@asynccontextmanager
async def assert_no_task_leaks() -> AsyncIterator[None]:
    """Fail and clean up when the enclosed behavior leaves asyncio tasks running."""

    current = asyncio.current_task()
    before = {task for task in asyncio.all_tasks() if task is not current}
    try:
        yield
    finally:
        await asyncio.sleep(0)
        after = {
            task
            for task in asyncio.all_tasks()
            if task is not current and task not in before and not task.done()
        }
        if after:
            for task in after:
                task.cancel()
            await asyncio.gather(*after, return_exceptions=True)
            names = ", ".join(sorted(task.get_name() for task in after))
            raise AssertionError(f"asyncio task leak: {names}")
