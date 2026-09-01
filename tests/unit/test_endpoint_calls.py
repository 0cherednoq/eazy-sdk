from __future__ import annotations

import asyncio
from typing import Annotated, Any, assert_type

from eazy_sdk import AsyncApi, SyncApi, api
from eazy_sdk.clients.async_client import _AsyncClientCore
from eazy_sdk.clients.executor import ExecutionRuntime
from eazy_sdk.clients.sync_client import _SyncClientCore
from eazy_sdk.handlers import (
    AutomaticHeaderPolicy,
    CapabilityLevel,
    EmitOptions,
    HandlerProfile,
    RedirectControl,
)
from eazy_sdk.request import Query
from eazy_sdk.request.prepared import BufferedBody, HttpProtocol, PreparedRequest
from eazy_sdk.response import (
    Headers,
    Json,
    NormalizedResponse,
    ResponseEnvelope,
    Responses,
    Success,
)

RESPONSES: Responses[dict[str, object]] = Responses(success=(Success(200, Json(dict)),))


class SyncSearchApi(SyncApi):
    @api.get("/search", operation_id="search", responses=RESPONSES)
    def search(
        self,
        *,
        term: Annotated[str, Query("q")],
    ) -> dict[str, object]:
        raise AssertionError("decorated method bodies are never executed")


class AsyncSearchApi(AsyncApi):
    @api.get("/search", operation_id="search", responses=RESPONSES)
    async def search(
        self,
        *,
        term: Annotated[str, Query("q")],
    ) -> dict[str, object]:
        raise AssertionError("decorated method bodies are never executed")


CAPABILITIES = HandlerProfile(
    protocols=frozenset({HttpProtocol.HTTP_1_1}),
    exact_target=CapabilityLevel.CAPTURE_VERIFIED,
    header_order=CapabilityLevel.CAPTURE_VERIFIED,
    header_casing=CapabilityLevel.CAPTURE_VERIFIED,
    duplicate_headers=CapabilityLevel.CAPTURE_VERIFIED,
    preencoded_body=CapabilityLevel.CAPTURE_VERIFIED,
    manual_cookie_field=CapabilityLevel.CAPTURE_VERIFIED,
    automatic_headers=AutomaticHeaderPolicy.MATERIALIZED,
    redirects=RedirectControl.FORCED_OFF,
    replayable_streams=CapabilityLevel.CAPTURE_VERIFIED,
)


def _response() -> NormalizedResponse[object]:
    return NormalizedResponse(
        200,
        "https://api.test/search?q=museum",
        "GET",
        Headers((("content-type", "application/json"),)),
        b'{"count":1}',
    )


def test_sync_and_async_methods_share_the_operation_pipeline() -> None:
    seen: list[bytes] = []

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        seen.append(request.target)
        return _response()

    async def emit_async(
        request: PreparedRequest, *, options: EmitOptions
    ) -> NormalizedResponse[object]:
        seen.append(request.target)
        return _response()

    sync = SyncSearchApi(_SyncClientCore(ExecutionRuntime(CAPABILITIES, emit, "https://api.test")))
    asynchronous = AsyncSearchApi(
        _AsyncClientCore(ExecutionRuntime(CAPABILITIES, emit_async, "https://api.test"))
    )

    sync_result = sync.search(term="museum")
    async_result = asyncio.run(asynchronous.search(term="museum"))
    envelope = sync.search.with_response(term="museum")

    assert_type(sync_result, dict[str, object])
    assert_type(envelope, ResponseEnvelope[dict[str, object], Any])
    assert sync_result == async_result == envelope.value == {"count": 1}
    assert seen == [b"/search?q=museum", b"/search?q=museum", b"/search?q=museum"]


def test_generic_request_uses_the_same_preparation_pipeline() -> None:
    seen: list[PreparedRequest] = []

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        seen.append(request)
        return NormalizedResponse(
            200,
            "https://api.test/raw",
            "POST",
            Headers((("content-type", "text/plain"),)),
            b"ok",
        )

    client = _SyncClientCore(ExecutionRuntime(CAPABILITIES, emit))
    response = client.request(
        "POST",
        "https://api.test/raw",
        params={"tag": "a,b"},
        headers={"X-Test": "yes"},
        json={"name": "museum"},
    )

    assert response.body == b"ok"
    assert seen[0].target == b"/raw?tag=a%2Cb"
    assert isinstance(seen[0].body, BufferedBody)
    assert seen[0].body.content == b'{"name":"museum"}'
