"""Synchronous effect runner over the shared execution core."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from eazy_sdk.auth.lifecycle import LifecycleGraph
from eazy_sdk.compile.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk.preparation import PreparedCall, PrepareOptions
from eazy_sdk.response import NormalizedResponse, ResponseEnvelope

from ._core import _UNSET, _ClientCore, _raw_call, _SyncRunner
from .base import CallOptions
from .executor import ExecutionRuntime, envelope


class _SyncClientCore[TRaw = object](_ClientCore[TRaw]):
    def __init__(
        self,
        runtime: ExecutionRuntime,
        *,
        raw: object | None = None,
        default_options: CallOptions | None = None,
        resolution_graph: LifecycleGraph | None = None,
        bind_sdk: bool = True,
        runner: _SyncRunner | None = None,
    ) -> None:
        super().__init__(
            runtime,
            raw=raw,
            default_options=default_options,
            resolution_graph=resolution_graph,
            bind_sdk=bind_sdk,
        )
        self._runner = runner or _SyncRunner()

    def _scoped(self, graph: LifecycleGraph) -> _SyncClientCore[TRaw]:
        return _SyncClientCore(
            self._runtime,
            raw=None,
            default_options=self._default_options,
            resolution_graph=graph,
            bind_sdk=False,
            runner=self._runner,
        )

    def _execute_operation[T](
        self,
        declaration: _OperationDeclaration[T],
        values: dict[str, object],
        *,
        options: CallOptions | None = None,
        with_response: bool,
    ) -> T | ResponseEnvelope[T, TRaw]:
        call = declaration.call(values)
        result = self._run(call, options)
        if with_response:
            return envelope(result)
        return cast(T, result.value)

    def request(
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
        return cast(NormalizedResponse[TRaw], self._run(call, options).value)

    def _prepare_operation[T](
        self,
        declaration: _OperationDeclaration[T],
        values: dict[str, object],
        *,
        options: PrepareOptions,
    ) -> PreparedCall:
        return self._runner.run(
            self._core.prepare(declaration.call(values), options=self._prepare_options(options))
        )

    def close(self) -> None:
        try:
            close = getattr(self.raw, "close", None)
            if close is not None:
                close()
        finally:
            self._runner.close()

    def __enter__(self) -> _SyncClientCore[TRaw]:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _run[T](
        self,
        call: _OperationCall[T],
        options: CallOptions | None,
    ) -> Any:
        return self._runner.run(
            self._core.execute(call, options=options or self._default_options)
        )


__all__: list[str] = []
