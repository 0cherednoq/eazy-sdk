"""Phase 46: one execution specification, two drivers, and no private event loop."""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys
import threading
import time
from typing import Annotated, Any

import pytest
from zapros import BaseHandler, Request, Response

from eazy_sdk import Client, ClientConfig, Json, Path, Resilience, RetryPolicy, SyncApi, api
from eazy_sdk.driver import SynchronousSuspensionError, driving_synchronously, run_sync, sleep

BASE = "https://drivers.test"


class Handler(BaseHandler):
    def __init__(self) -> None:
        self.loops: list[int | None] = []

    def handle(self, request: Request) -> Response:
        try:
            self.loops.append(id(asyncio.get_running_loop()))
        except RuntimeError:
            self.loops.append(None)
        return Response(
            200,
            [("Content-Type", "application/json")],
            content=b'{"name":"Ada"}',
            request=request,
        )

    def close(self) -> None:
        return None


class Users(SyncApi):
    @api.get("/users/{user_id}", response=Json())
    def get(self, *, user_id: Annotated[int, Path()]) -> dict[str, object]:
        raise NotImplementedError


def test_a_synchronous_call_runs_inside_an_active_event_loop() -> None:
    handler = Handler()

    async def caller() -> dict[str, object]:
        running = id(asyncio.get_running_loop())
        with Client(base_url=BASE, handler=handler) as client:
            value = Users(client).get(user_id=1)
        assert handler.loops == [running], "the call kept the caller's loop"
        return value

    assert asyncio.run(caller()) == {"name": "Ada"}


def test_a_synchronous_call_outside_a_loop_creates_none() -> None:
    handler = Handler()
    with Client(base_url=BASE, handler=handler) as client:
        assert Users(client).get(user_id=1) == {"name": "Ada"}
    assert handler.loops == [None]

    seen: list[object] = []

    def worker() -> None:
        with Client(base_url=BASE, handler=Handler()) as client:
            Users(client).get(user_id=2)
        try:
            seen.append(asyncio.get_event_loop())
        except RuntimeError:
            seen.append(None)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen == [None], "no loop was installed in the worker thread"


def test_the_runner_and_its_conflict_error_are_gone() -> None:
    from eazy_sdk.clients import _core

    assert not hasattr(_core, "_SyncRunner")
    assert not hasattr(_core, "_close_runner")
    import eazy_sdk.clients as clients

    assert not hasattr(clients, "EventLoopConflictError")
    assert "SynchronousSuspensionError" in clients.__all__
    for module in (_core, sys.modules["eazy_sdk.clients.sync_client"]):
        source = pathlib.Path(inspect.getfile(module)).read_text(encoding="utf-8")
        assert "asyncio.Runner" not in source
        assert "_loop" not in source, "no private asyncio attribute is read"


def test_the_synchronous_driver_completes_a_never_suspending_coroutine() -> None:
    async def work() -> str:
        async def inner() -> str:
            return "value"

        assert driving_synchronously()
        return await inner()

    assert run_sync(work()) == "value"
    assert not driving_synchronously()


def test_the_synchronous_driver_says_so_when_something_really_suspends() -> None:
    class Suspends:
        """What a callback awaiting real asynchronous I/O looks like to the driver."""

        def __await__(self) -> Any:
            yield None

    async def work() -> None:
        await Suspends()

    with pytest.raises(SynchronousSuspensionError, match="AsyncClient"):
        run_sync(work())

    async def needs_a_loop() -> None:
        await asyncio.sleep(0.01)

    # asyncio refuses even to build the wait without a loop, which is just as clear.
    with pytest.raises(RuntimeError, match="no running event loop"):
        run_sync(needs_a_loop())


def test_the_synchronous_driver_propagates_the_error_the_call_raised() -> None:
    async def work() -> None:
        raise ValueError("declared")

    with pytest.raises(ValueError, match="declared"):
        run_sync(work())


def test_a_wait_blocks_the_thread_under_the_synchronous_driver() -> None:
    async def work() -> float:
        start = time.perf_counter()
        await sleep(0.02)
        return time.perf_counter() - start

    assert run_sync(work()) >= 0.015


@pytest.mark.asyncio
async def test_the_same_wait_yields_to_the_loop_under_the_asynchronous_driver() -> None:
    ticks = 0

    async def counter() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(counter())
    await sleep(0.02)
    task.cancel()
    assert ticks > 1, "the loop kept running while the wait was pending"


def test_the_retry_backoff_of_a_synchronous_call_needs_no_loop() -> None:
    class Flaky(BaseHandler):
        def __init__(self) -> None:
            self.calls = 0

        def handle(self, request: Request) -> Response:
            self.calls += 1
            status = 503 if self.calls == 1 else 200
            return Response(
                status,
                [("Content-Type", "application/json")],
                content=b'{"name":"Ada"}',
                request=request,
            )

        def close(self) -> None:
            return None

    handler = Flaky()
    config = ClientConfig(
        resilience=Resilience(
            retry=RetryPolicy.safe(max_attempts=2, base_delay=0.001, max_delay=0.001),
            auth_retries=0,
        )
    )
    with Client(base_url=BASE, handler=handler, config=config) as client:
        assert Users(client).get(user_id=1) == {"name": "Ada"}
    assert handler.calls == 2


def test_the_drivers_hold_no_decision_logic() -> None:
    source = pathlib.Path(
        inspect.getfile(sys.modules["eazy_sdk.driver"])
    ).read_text(encoding="utf-8")
    for decision in ("RetryTransition", "RedirectTransition", "AttemptState", "budget"):
        assert decision not in source, decision


def test_the_websocket_runtime_stays_asynchronous_on_purpose() -> None:
    """WebSocket has no synchronous driver: the connection itself is the state."""

    import eazy_sdk.websocket as websocket

    assert not hasattr(websocket, "WsClient")
    assert hasattr(websocket, "AsyncWsClient")
    source = pathlib.Path(
        inspect.getfile(sys.modules["eazy_sdk.websocket.runtime"])
    ).read_text(encoding="utf-8")
    assert "run_sync" not in source
