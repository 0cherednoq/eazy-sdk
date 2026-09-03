"""Shared state and helpers of the sync/async clients (one definition, two effect runners)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import unquote_plus, urlsplit

from eazy_sdk.auth.lifecycle import LifecycleGraph
from eazy_sdk.compile.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk.compile.input import InputField, MethodInputSchema
from eazy_sdk.core.http import RequestLocation
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.preparation import PrepareOptions
from eazy_sdk.protection.advanced import InstallableProtection
from eazy_sdk.request import (
    BytesBody,
    Cookie,
    Header,
    JsonBody,
    Query,
    RequestBody,
)
from eazy_sdk.response import NormalizedResponse

from .base import CallOptions, EventLoopConflictError
from .executor import ExecutionCore, ExecutionRuntime, _protection_identities

_UNSET = object()


class _ClientCore[TRaw = object]:
    """Runtime ownership, SDK binding and protection invalidation shared by both clients."""

    def __init__(
        self,
        runtime: ExecutionRuntime,
        *,
        raw: object | None = None,
        default_options: CallOptions | None = None,
        resolution_graph: LifecycleGraph | None = None,
        bind_sdk: bool = True,
    ) -> None:
        self._runtime = runtime
        self._resolution_graph = resolution_graph
        self._core = ExecutionCore(runtime, resolution_graph=resolution_graph)
        self._default_options = default_options or CallOptions()
        self._can_bind_sdk = bind_sdk
        self.raw = raw

    def bind_sdk[TSdk](self, sdk_factory: Callable[[Any], TSdk]) -> TSdk:
        """Create an SDK root and register its scoped lifecycle factory."""

        if not callable(sdk_factory):
            raise TypeError("SDK binding requires a callable root factory")
        if self._can_bind_sdk:
            self._runtime.auth.bind_sdk_factory(
                lambda graph: sdk_factory(self._scoped(graph))
            )
        return sdk_factory(self)

    def _scoped(self, graph: LifecycleGraph) -> _ClientCore[TRaw]:
        raise NotImplementedError

    def invalidate_protection(self, *targets: InstallableProtection | str) -> int:
        """Drop cached protection solutions; pass guards or policy names to narrow it.

        Returns the number of dropped managed states. Without arguments every cached
        solution of this client's session is dropped; the next matching challenge is
        solved again.
        """

        return self._runtime.invalidate_protection(_protection_identities(targets))

    def _prepare_options(self, options: PrepareOptions) -> PrepareOptions:
        if options.call_options is not None:
            return options
        return replace(options, call_options=self._default_options)


class _SyncRunner:
    """Per-thread reusable ``asyncio.Runner`` for the synchronous client.

    One event loop per thread is created lazily and reused by every call, instead of
    paying for ``asyncio.run()`` on each request. Calling from a thread that already runs
    an event loop raises ``EventLoopConflictError``: use ``AsyncClient`` there.
    """

    def __init__(self) -> None:
        self._local = threading.local()
        self._runners: list[asyncio.Runner] = []
        self._lock = threading.Lock()
        self._closed = False

    def run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            coroutine.close()
            raise EventLoopConflictError(
                "the synchronous Client cannot run inside an active event loop; "
                "use AsyncClient from asynchronous code"
            )
        runner = getattr(self._local, "runner", None)
        if runner is None or self._closed:
            if self._closed:
                coroutine.close()
                raise RuntimeError("Eazy SDK Client is closed")
            runner = asyncio.Runner()
            self._local.runner = runner
            with self._lock:
                self._runners.append(runner)
        try:
            return runner.run(coroutine)
        finally:
            # Mirror ``asyncio.run()``: the reusable loop must not stay the thread's current
            # loop between calls, otherwise later ``asyncio`` users would pick it up.
            asyncio.set_event_loop(None)

    def close(self) -> None:
        self._closed = True
        with self._lock:
            runners, self._runners = self._runners, []
        self._local = threading.local()
        for runner in runners:
            _close_runner(runner)

    def __del__(self) -> None:
        # A client that was never closed must not leak event-loop sockets.
        with contextlib.suppress(Exception):
            self.close()


def _raw_call(
    method: str,
    url: str,
    params: Mapping[str, object] | None,
    headers: Mapping[str, object] | None,
    cookies: Mapping[str, object] | None,
    json: object,
    content: object,
) -> _OperationCall[NormalizedResponse[object]]:
    if json is not _UNSET and content is not _UNSET:
        raise ValueError("json and content are mutually exclusive")
    _validate_raw_query(url, params)
    body_descriptor: RequestBody | None = (
        JsonBody() if json is not _UNSET else BytesBody() if content is not _UNSET else None
    )
    fields: list[InputField] = []
    values: dict[str, object] = {}
    for prefix, source, descriptor in (
        ("query", params, Query),
        ("header", headers, Header),
        ("cookie", cookies, Cookie),
    ):
        for index, (name, value) in enumerate((source or {}).items()):
            key = f"_{prefix}_{index}"
            placement = descriptor(name)
            location = {
                "query": RequestLocation.QUERY,
                "header": RequestLocation.HEADER,
                "cookie": RequestLocation.COOKIE,
            }[prefix]
            fields.append(InputField(key, name, object, False, location, placement))
            values[key] = value
    if body_descriptor is not None:
        fields.append(
            InputField(
                "_body",
                "_body",
                object,
                False,
                RequestLocation.BODY,
                body_descriptor,
            )
        )
        values["_body"] = json if json is not _UNSET else content
    declaration: _OperationDeclaration[NormalizedResponse[object]] = _OperationDeclaration(
        operation_id=f"raw:{method.upper()}:{url}",
        method=method,
        path=url,
        input_fields=tuple(fields),
        input_schema=MethodInputSchema(tuple(fields)),
        result_type=NormalizedResponse[object],
        responses=object(),
        scope=RequestScope(
            methods=frozenset({method.upper()}),
            operation_ids=frozenset({f"raw:{method.upper()}:{url}"}),
        ),
        raw_response=True,
    )
    return declaration.call(values)


def _validate_raw_query(url: str, params: Mapping[str, object] | None) -> None:
    names = [
        unquote_plus(field.partition("=")[0]) for field in urlsplit(url).query.split("&") if field
    ]
    if params is not None:
        multi_items = getattr(params, "multi_items", None)
        supplied = tuple(multi_items()) if callable(multi_items) else tuple(params.items())
        names.extend(str(name) for name, _ in supplied)
        for name, value in params.items():
            if isinstance(value, Mapping | list | tuple | set | frozenset):
                raise ValueError(
                    f"raw query parameter {name!r} requires an explicit single-value codec"
                )
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate raw query names are unsupported: {duplicates}")


def _close_runner(runner: asyncio.Runner) -> None:
    """Close a runner; when another loop runs in this thread, close its loop directly.

    ``Runner.close()`` awaits shutdown coroutines on the runner's loop, which is impossible
    while a different loop is running (for example when a forgotten client is garbage
    collected inside asynchronous code). Closing the idle loop directly releases its
    sockets without creating coroutines that could never be awaited.
    """

    loop = getattr(runner, "_loop", None)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        with contextlib.suppress(RuntimeError):
            runner.close()
            return
    if loop is not None and not loop.is_closed() and not loop.is_running():
        with contextlib.suppress(RuntimeError):
            loop.close()
