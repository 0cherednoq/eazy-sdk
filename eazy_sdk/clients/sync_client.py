"""Synchronous effect runner over the shared execution core."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, cast
from urllib.parse import unquote_plus, urlsplit

from eazy_sdk._internal.http import RequestLocation
from eazy_sdk._internal.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk._internal.http_plan import RequestScope
from eazy_sdk._internal.input import InputField, MethodInputSchema
from eazy_sdk.accounts.session import LifecycleGraph
from eazy_sdk.request import (
    BytesBody,
    Cookie,
    Header,
    JsonBody,
    Query,
    RequestBody,
)
from eazy_sdk.response import NormalizedResponse, ResponseEnvelope

from .base import CallOptions
from .executor import ExecutionCore, ExecutionRuntime, envelope

_UNSET = object()


class _SyncClientCore[TRaw = object]:
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

    def get(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> NormalizedResponse[TRaw]:
        return self.request("POST", url, **kwargs)

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
    ) -> Any:
        return asyncio.run(self._core.execute(call, options=options or self._default_options))


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


__all__: list[str] = []
