"""Asynchronous effect runner over the shared execution core."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, cast

from eazy_sdk.auth.lifecycle import LifecycleGraph
from eazy_sdk.compile.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk.preparation import PreparedCall, PrepareOptions
from eazy_sdk.response import NormalizedResponse, ResponseEnvelope

from ._core import _UNSET, _ClientCore, _raw_call
from .base import CallOptions
from .executor import envelope


class _AsyncClientCore[TRaw = object](_ClientCore[TRaw]):
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
        return await self._core.prepare(
            declaration.call(values), options=self._prepare_options(options)
        )

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
