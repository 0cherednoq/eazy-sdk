from __future__ import annotations

import asyncio
from typing import Any, cast

from eazy_sdk import ApiDefaults, AsyncApi, SyncApi, api
from eazy_sdk._internal.http_compiler import compile_endpoint
from eazy_sdk.clients import CallOptions
from eazy_sdk.clients.async_client import _AsyncClientCore
from eazy_sdk.clients.sync_client import _SyncClientCore
from eazy_sdk.ext import ExecutionRuntime, PreparedRequest
from eazy_sdk.handlers import EmitOptions, TransportFailure
from eazy_sdk.response import Headers, Json, NormalizedResponse, Responses, Success
from tests.rewrite.test_phase08_execution import CAPABILITIES

RESPONSES = Responses[dict[str, bool]](
    success=(Success(200, Json(dict)),),
)


class TraceApi(SyncApi):
    @api.get("/trace", operation_id="phase26.trace", responses=RESPONSES)
    def trace(self, *, options: CallOptions | None = None) -> dict[str, bool]:
        raise NotImplementedError


class AsyncTraceApi(AsyncApi):
    @api.get("/trace", operation_id="phase26.trace", responses=RESPONSES)
    async def trace(self, *, options: CallOptions | None = None) -> dict[str, bool]:
        raise NotImplementedError


def _contract() -> Any:
    return cast(Any, TraceApi.trace).resolve(ApiDefaults())


def _response() -> NormalizedResponse[object]:
    return NormalizedResponse(
        200,
        "https://api.test/trace",
        "GET",
        Headers((("content-type", "application/json"),)),
        b'{"ok":true}',
    )


def _observer(trace: list[tuple[str, object]]) -> Any:
    def observe(phase: str, value: object | None) -> None:
        if phase == "start_attempt":
            assert isinstance(value, dict)
            trace.append((phase, (value["number"], value["kind"])))
        elif phase == "prepared":
            trace.append((phase, "request"))
        else:
            trace.append((phase, value))

    return observe


def test_http_transition_trace_and_fingerprint_are_frozen_before_stage_extraction() -> None:
    sync_trace: list[tuple[str, object]] = []
    async_trace: list[tuple[str, object]] = []
    sync_requests: list[PreparedRequest] = []
    async_requests: list[PreparedRequest] = []

    def sync_send(
        request: PreparedRequest, *, options: EmitOptions
    ) -> NormalizedResponse[object]:
        sync_requests.append(request)
        if len(sync_requests) == 1:
            raise TransportFailure("baseline", "emit", 1, OSError("retry"))
        return _response()

    async def async_send(
        request: PreparedRequest, *, options: EmitOptions
    ) -> NormalizedResponse[object]:
        async_requests.append(request)
        if len(async_requests) == 1:
            raise TransportFailure("baseline", "emit", 1, OSError("retry"))
        return _response()

    options = CallOptions(max_attempts=2, transport_retries=1)
    sync_api = TraceApi(
        _SyncClientCore(
            ExecutionRuntime(
                CAPABILITIES,
                sync_send,
                "https://api.test",
                observer=_observer(sync_trace),
            )
        )
    )
    async_api = AsyncTraceApi(
        _AsyncClientCore(
            ExecutionRuntime(
                CAPABILITIES,
                async_send,
                "https://api.test",
                observer=_observer(async_trace),
            )
        )
    )

    assert sync_api.trace(options=options) == {"ok": True}
    assert asyncio.run(async_api.trace(options=options)) == {"ok": True}

    expected = [
        ("start_attempt", (1, "initial")),
        ("prepared", "request"),
        ("start_attempt", (2, "transport-retry")),
        ("prepared", "request"),
        ("emit", 2),
    ]
    assert sync_trace == async_trace == expected
    assert len({id(request) for request in sync_requests}) == 2
    assert len({id(request) for request in async_requests}) == 2
    assert {request.target for request in (*sync_requests, *async_requests)} == {b"/trace"}
    assert compile_endpoint(_contract()).plan.fingerprint == (
        "3e954fe8fad3118ff4cdd6860bd9e35d426e352df748bc52edd3eb6a941fb46d"
    )
