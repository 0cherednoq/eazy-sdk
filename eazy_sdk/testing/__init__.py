"""Zapros-native recording handlers for SDK tests."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping

from zapros import AsyncBaseHandler, BaseHandler, Request, Response

type ResponseFactory = Callable[[Request], Response]
type AsyncResponseFactory = Callable[[Request], Response | Awaitable[Response]]


class RecordingHandler(BaseHandler):
    """Record actual Zapros requests and return deterministic test responses."""

    def __init__(
        self,
        respond: ResponseFactory | None = None,
        *,
        status: int = 200,
        headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes = b"",
    ) -> None:
        self._respond = respond
        self._status = status
        self._headers = headers
        self._content = content
        self._requests: list[Request] = []
        self.close_calls = 0

    @property
    def requests(self) -> tuple[Request, ...]:
        return tuple(self._requests)

    @property
    def last_request(self) -> Request:
        if not self._requests:
            raise AssertionError("no request has been recorded")
        return self._requests[-1]

    def handle(self, request: Request) -> Response:
        self._requests.append(request)
        if self._respond is not None:
            return self._respond(request)
        return Response(
            self._status,
            self._headers,
            content=self._content,
            request=request,
        )

    def close(self) -> None:
        self.close_calls += 1

    def assert_count(self, expected: int) -> None:
        actual = len(self._requests)
        if actual != expected:
            raise AssertionError(f"expected {expected} requests, recorded {actual}")

    def assert_request(
        self,
        *,
        index: int = -1,
        method: str | None = None,
        url: str | None = None,
    ) -> Request:
        try:
            request = self._requests[index]
        except IndexError:
            raise AssertionError(f"no recorded request at index {index}") from None
        _assert_request(request, method=method, url=url)
        return request


class AsyncRecordingHandler(AsyncBaseHandler):
    """Async equivalent of :class:`RecordingHandler`."""

    def __init__(
        self,
        respond: AsyncResponseFactory | None = None,
        *,
        status: int = 200,
        headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes = b"",
    ) -> None:
        self._respond = respond
        self._status = status
        self._headers = headers
        self._content = content
        self._requests: list[Request] = []
        self.close_calls = 0

    @property
    def requests(self) -> tuple[Request, ...]:
        return tuple(self._requests)

    @property
    def last_request(self) -> Request:
        if not self._requests:
            raise AssertionError("no request has been recorded")
        return self._requests[-1]

    async def ahandle(self, request: Request) -> Response:
        self._requests.append(request)
        if self._respond is not None:
            response = self._respond(request)
            return await response if inspect.isawaitable(response) else response
        return Response(
            self._status,
            self._headers,
            content=self._content,
            request=request,
        )

    async def aclose(self) -> None:
        self.close_calls += 1

    def assert_count(self, expected: int) -> None:
        actual = len(self._requests)
        if actual != expected:
            raise AssertionError(f"expected {expected} requests, recorded {actual}")

    def assert_request(
        self,
        *,
        index: int = -1,
        method: str | None = None,
        url: str | None = None,
    ) -> Request:
        try:
            request = self._requests[index]
        except IndexError:
            raise AssertionError(f"no recorded request at index {index}") from None
        _assert_request(request, method=method, url=url)
        return request


def _assert_request(
    request: Request,
    *,
    method: str | None,
    url: str | None,
) -> None:
    if method is not None and request.method != method.upper():
        raise AssertionError(f"expected method {method.upper()!r}, got {request.method!r}")
    actual_url = str(request.url)
    if url is not None and actual_url != url:
        raise AssertionError(f"expected URL {url!r}, got {actual_url!r}")


__all__ = ["AsyncRecordingHandler", "RecordingHandler"]
