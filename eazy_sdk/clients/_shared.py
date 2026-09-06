"""Shared state and helpers of the sync/async clients (one definition, two effect runners)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import unquote_plus, urlsplit

from eazy_sdk.auth.lifecycle import LifecycleGraph
from eazy_sdk.compile.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk.compile.input import InputField, MethodInputSchema
from eazy_sdk.core.http import RequestLocation
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.identity import _IdentityScope
from eazy_sdk.preparation import PrepareOptions
from eazy_sdk.protection.advanced import InstallableProtection
from eazy_sdk.request.descriptors import BytesBody, JsonBody, RequestBody
from eazy_sdk.request.params import Cookie, Header, Query
from eazy_sdk.response import NormalizedResponse
from eazy_sdk.serialization import Serialization

from .base import CallOptions
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

    def _core_for(
        self,
        identity: _IdentityScope | None,
        serialization: Serialization | None = None,
    ) -> ExecutionCore:
        """Execution core for one caller and one SDK; the transport runtime stays shared."""

        if identity is None and serialization is None:
            return self._core
        return ExecutionCore(
            self._runtime,
            identity=identity,
            serialization=serialization,
            resolution_graph=self._resolution_graph,
        )

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
