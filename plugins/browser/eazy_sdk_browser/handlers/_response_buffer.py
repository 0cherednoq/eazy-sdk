"""Transport-neutral bounded response buffer used by browser adapters."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque

from eazy_sdk_browser.network import ResponseView

RESPONSE_POLL = 0.05


class ResponseBuffer:
    """Keep captured replies under a byte budget without renumbering marks."""

    def __init__(self) -> None:
        self._responses: deque[tuple[int, ResponseView]] = deque()
        self._buffered = 0
        self._seen = 0
        self._arrived = asyncio.Event()

    def mark(self) -> int:
        return self._seen

    def add(self, view: ResponseView, *, max_total_bytes: int) -> None:
        self._responses.append((self._seen, view))
        self._seen += 1
        self._buffered += len(view.body)
        while self._buffered > max_total_bytes and len(self._responses) > 1:
            _, evicted = self._responses.popleft()
            self._buffered -= len(evicted.body)
        self._arrived.set()

    async def wait(
        self, url_contains: str, *, within: float, since: int = 0
    ) -> ResponseView | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        while True:
            found = self._find(url_contains, since)
            if found is not None:
                return found
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            self._arrived.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._arrived.wait(), timeout=min(remaining, RESPONSE_POLL))

    def _find(self, url_contains: str, since: int) -> ResponseView | None:
        for position, response in reversed(self._responses):
            if position < since:
                return None
            if url_contains in response.url:
                return response
        return None


__all__ = ["ResponseBuffer"]
