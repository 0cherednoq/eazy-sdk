"""Two drivers over one execution specification.

The core describes what happens next; a driver only carries it out. The asynchronous driver
is the event loop itself. The synchronous driver is this module's :func:`run_sync`, which
steps the same coroutine to completion **without creating an event loop at all** — because
on a synchronous transport nothing in the pipeline actually suspends: every ``await`` there
resolves in place.

Two consequences follow, and both are the point of the phase. A synchronous call now works
inside an already-running loop, because it never installs one. And the places that really do
wait — retry backoff, rate-limit delay, contended protection locks — go through
:func:`sleep`, which blocks the thread under the synchronous driver and yields to the loop
under the asynchronous one.

What the synchronous driver cannot do is run genuine asynchronous I/O inside a callback:
a solver, middleware or auth service that awaits a network round trip of its own suspends,
and :class:`SynchronousSuspensionError` says so instead of quietly spinning a private loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine, Generator
from contextvars import ContextVar
from typing import Any, cast

from eazy_sdk.core.errors import EazySdkError

_SYNC_DRIVER: ContextVar[bool] = ContextVar("eazy_sdk_sync_driver", default=False)


class SynchronousSuspensionError(EazySdkError, RuntimeError):
    """A synchronous call reached code that needs an event loop to make progress."""


class _Sleep:
    """A delay served by whichever driver is running: the thread's, or the loop's."""

    __slots__ = ("seconds",)

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def __await__(self) -> Generator[Any, Any]:
        if self.seconds <= 0:
            return
        if _SYNC_DRIVER.get():
            time.sleep(self.seconds)
            return
        yield from asyncio.sleep(self.seconds).__await__()


async def sleep(seconds: float) -> None:
    """Wait without deciding which driver is running."""

    await _Sleep(seconds)


def driving_synchronously() -> bool:
    """Whether the current call is being stepped by :func:`run_sync`."""

    return _SYNC_DRIVER.get()


def run_sync[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Step one execution to completion on this thread, without an event loop."""

    token = _SYNC_DRIVER.set(True)
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return cast(T, stop.value)
    finally:
        _SYNC_DRIVER.reset(token)
    coroutine.close()
    raise SynchronousSuspensionError(
        "the synchronous client reached code that awaits real asynchronous work; "
        "use AsyncClient, or make that solver, middleware or auth service synchronous"
    )


__all__ = ["SynchronousSuspensionError"]
