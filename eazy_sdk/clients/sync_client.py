"""Synchronous effect runner over the shared execution core."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from eazy_sdk.auth.lifecycle import LifecycleGraph
from eazy_sdk.compile.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk.driver import run_sync
from eazy_sdk.identity import _IdentityScope
from eazy_sdk.preparation import PreparedCall, PrepareOptions
from eazy_sdk.response import NormalizedResponse, ResponseEnvelope
from eazy_sdk.serialization import Serialization

from ._shared import _UNSET, _ClientCore, _raw_call
from .base import CallOptions
from .executor import envelope


class _SyncClientCore[TRaw = object](_ClientCore[TRaw]):
    """The synchronous driver: one execution specification, stepped on this thread."""

    def _scoped(self, graph: LifecycleGraph) -> _SyncClientCore[TRaw]:
        return _SyncClientCore(
            self._runtime,
            raw=None,
            default_options=self._default_options,
            resolution_graph=graph,
            bind_sdk=False,
        )

    def _execute_operation[T](
        self,
        declaration: _OperationDeclaration[T],
        values: dict[str, object],
        *,
        options: CallOptions | None = None,
        with_response: bool,
        identity: _IdentityScope | None = None,
        serialization: Serialization | None = None,
    ) -> T | ResponseEnvelope[T, TRaw]:
        call = declaration.call(values)
        result = self._run(call, options, identity, serialization)
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
        return cast(NormalizedResponse[TRaw], self._run(call, options, None, None).value)

    def _prepare_operation[T](
        self,
        declaration: _OperationDeclaration[T],
        values: dict[str, object],
        *,
        options: PrepareOptions,
        identity: _IdentityScope | None = None,
        serialization: Serialization | None = None,
    ) -> PreparedCall:
        return run_sync(
            self._core_for(identity, serialization).prepare(
                declaration.call(values), options=self._prepare_options(options)
            )
        )

    def close(self) -> None:
        close = getattr(self.raw, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> _SyncClientCore[TRaw]:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _run[T](
        self,
        call: _OperationCall[T],
        options: CallOptions | None,
        identity: _IdentityScope | None = None,
        serialization: Serialization | None = None,
    ) -> Any:
        return run_sync(
            self._core_for(identity, serialization).execute(
                call, options=options or self._default_options
            )
        )


__all__: list[str] = []
