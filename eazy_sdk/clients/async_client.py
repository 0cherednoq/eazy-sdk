"""Asynchronous effect runner over the shared execution core."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, cast

from eazy_sdk._internal.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk.accounts.session import LifecycleGraph
from eazy_sdk.preparation import PreparedCall, PrepareOptions
from eazy_sdk.response import NormalizedResponse, ResponseEnvelope

from .base import CallOptions
from .executor import ExecutionCore, ExecutionRuntime, envelope
from .sync_client import _UNSET, _raw_call


class _AsyncClientCore[TRaw = object]:
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

    def _scoped(self, graph: LifecycleGraph) -> _AsyncClientCore[TRaw]:
        return _AsyncClientCore(
            self._runtime,
            raw=None,
            default_options=self._default_options,
            resolution_graph=graph,
            bind_sdk=False,
        )

    async def _execute_operation[T](
        self,
        declaration: _OperationDeclaration[T],
        values: dict[str, object],
        *,
        options: CallOptions | None = None,
        with_response: bool,
    ) -> T | ResponseEnvelope[T, TRaw]:
        call = declaration.call(values)
        result = await self._run(call, options)
        if with_response:
            return envelope(result)
        return cast(T, result.value)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, object] | None = None,
        cookies: Mapping[str, object] | None = None,
        json: object = _UNSET,
        content: bytes | object = _UNSET,
        options: CallOptions | None = None,
    ) -> NormalizedResponse[TRaw]:
        call = _raw_call(method, url, params, headers, cookies, json, content)
        return cast(NormalizedResponse[TRaw], (await self._run(call, options)).value)

    async def _prepare_operation[T](
        self,
        declaration: _OperationDeclaration[T],
        values: dict[str, object],
        *,
        options: PrepareOptions,
    ) -> PreparedCall:
        effective = (
            options
            if options.call_options is not None
            else replace(options, call_options=self._default_options)
        )
        return await self._core.prepare(declaration.call(values), options=effective)

    async def get(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return await self.request("GET", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return await self.request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return await self.request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return await self.request("OPTIONS", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return await self.request("PATCH", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return await self.request("PUT", url, **kwargs)

    async def trace(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return await self.request("TRACE", url, **kwargs)

    async def aclose(self) -> None:
        close = getattr(self.raw, "aclose", None)
        if close is not None:
            await close()
            return
        close = getattr(self.raw, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    async def __aenter__(self) -> _AsyncClientCore[TRaw]:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def _run[T](
        self,
        call: _OperationCall[T],
        options: CallOptions | None,
    ) -> Any:
        return await self._core.execute(call, options=options or self._default_options)


__all__: list[str] = []
