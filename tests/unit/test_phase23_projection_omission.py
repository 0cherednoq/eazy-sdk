from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypedDict, Unpack, cast

import httpx
import msgspec
import pytest
from pydantic import BaseModel

from eazy_sdk import AsyncApi, AsyncClient, ClientConfig, OperationBindingError, api
from eazy_sdk.clients import RetryPolicy
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import BodyProjection, JsonBody
from eazy_sdk.response import Json, Responses, Success


@dataclass(frozen=True, slots=True)
class Reply:
    ok: bool


RESPONSES = Responses[Reply](success=(Success(200, Json(Reply)),))


class OptionalSource(TypedDict, total=False):
    page: int | None


class OptionalWire(TypedDict):
    page: int | None
    attempt: int


class RequiredSource(TypedDict):
    page: int


class RequiredWire(TypedDict):
    page: int


class MutableSource(TypedDict):
    values: list[str]


@dataclass
class DataclassMutableWire:
    values: list[str]


class PydanticMutableWire(BaseModel):
    values: list[str]


class MsgspecMutableWire(msgspec.Struct):
    values: list[str]


def _client(handler: Any, *, config: ClientConfig | None = None) -> AsyncClient:
    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    return AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=config,
    )


async def test_optional_projection_key_may_be_omitted() -> None:
    mapper_inputs: list[OptionalSource] = []
    requests: list[dict[str, object]] = []

    def project(source: OptionalSource) -> OptionalWire:
        mapper_inputs.append(source.copy())
        return {"page": source.get("page", 1), "attempt": 1}

    projection = BodyProjection(
        OptionalSource,
        OptionalWire,
        project,
        JsonBody(),
    )

    class SearchApi(AsyncApi):
        @api.put("/search", body=projection, responses=RESPONSES)
        async def search(self, **request: Unpack[OptionalSource]) -> Reply:
            raise NotImplementedError

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    async with client:
        result = await SearchApi(client).search()

    assert result == Reply(ok=True)
    assert mapper_inputs == [{}]
    assert requests == [{"page": 1, "attempt": 1}]


async def test_explicit_none_is_distinct_from_omission() -> None:
    mapper_inputs: list[OptionalSource] = []
    requests: list[dict[str, object]] = []

    def project(source: OptionalSource) -> OptionalWire:
        mapper_inputs.append(source.copy())
        return {"page": source.get("page", 1), "attempt": 1}

    projection = BodyProjection(
        OptionalSource,
        OptionalWire,
        project,
        JsonBody(),
    )

    class SearchApi(AsyncApi):
        @api.put("/search", body=projection, responses=RESPONSES)
        async def search(self, **request: Unpack[OptionalSource]) -> Reply:
            raise NotImplementedError

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    async with client:
        result = await SearchApi(client).search(page=None)

    assert result == Reply(ok=True)
    assert mapper_inputs == [{"page": None}]
    assert requests == [{"page": None, "attempt": 1}]


async def test_required_projection_key_fails_before_mapper_and_handler() -> None:
    mapper_calls = 0
    handler_calls = 0

    def project(source: RequiredSource) -> RequiredWire:
        nonlocal mapper_calls
        mapper_calls += 1
        return {"page": source["page"]}

    projection = BodyProjection(
        RequiredSource,
        RequiredWire,
        project,
        JsonBody(),
    )

    class SearchApi(AsyncApi):
        @api.post("/search", body=projection, responses=RESPONSES)
        async def search(self, **request: Unpack[RequiredSource]) -> Reply:
            raise NotImplementedError

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal handler_calls
        handler_calls += 1
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    async with client:
        with pytest.raises(OperationBindingError) as captured:
            await cast(Any, SearchApi(client).search)()

    assert captured.value.as_dict() == {
        "code": "missing_required",
        "operation_id": "search",
        "field": "page",
        "phase": "bind",
    }
    assert "body-projection.source.page" not in str(captured.value)
    assert "body-projection.source.page" not in repr(captured.value)
    assert mapper_calls == 0
    assert handler_calls == 0


async def test_binding_diagnostics_are_structured_and_do_not_expose_values() -> None:
    mapper_calls = 0
    handler_calls = 0
    secret = "do-not-leak"

    def project(source: OptionalSource) -> OptionalWire:
        nonlocal mapper_calls
        mapper_calls += 1
        return {"page": source.get("page", 1), "attempt": 1}

    projection = BodyProjection(
        OptionalSource,
        OptionalWire,
        project,
        JsonBody(),
    )

    class SearchApi(AsyncApi):
        @api.post("/search", body=projection, responses=RESPONSES)
        async def search(self, **request: Unpack[OptionalSource]) -> Reply:
            raise NotImplementedError

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal handler_calls
        handler_calls += 1
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    async with client:
        with pytest.raises(OperationBindingError) as invalid:
            await cast(Any, SearchApi(client).search)(page=secret)
        with pytest.raises(OperationBindingError) as unknown:
            await cast(Any, SearchApi(client).search)(private=secret)

    assert invalid.value.as_dict() == {
        "code": "invalid_value",
        "operation_id": "search",
        "field": "page",
        "phase": "bind",
    }
    assert unknown.value.as_dict() == {
        "code": "unknown_input",
        "operation_id": "search",
        "field": "private",
        "phase": "bind",
    }
    assert json.dumps(invalid.value.as_dict(), sort_keys=True) == (
        '{"code": "invalid_value", "field": "page", '
        '"operation_id": "search", "phase": "bind"}'
    )
    for error in (invalid.value, unknown.value):
        assert secret not in str(error)
        assert secret not in repr(error)
        assert error.__cause__ is None
    assert mapper_calls == 0
    assert handler_calls == 0


async def test_omitted_projection_key_stays_omitted_on_retry() -> None:
    mapper_inputs: list[OptionalSource] = []
    requests: list[dict[str, object]] = []

    def project(source: OptionalSource) -> OptionalWire:
        mapper_inputs.append(source.copy())
        return {
            "page": source.get("page", 1),
            "attempt": len(mapper_inputs),
        }

    projection = BodyProjection(
        OptionalSource,
        OptionalWire,
        project,
        JsonBody(),
    )

    class SearchApi(AsyncApi):
        @api.put("/search", body=projection, responses=RESPONSES)
        async def search(self, **request: Unpack[OptionalSource]) -> Reply:
            raise NotImplementedError

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(cast(dict[str, object], json.loads(request.content)))
        status = 503 if len(requests) == 1 else 200
        return httpx.Response(status, json={"ok": True})

    client = _client(
        handler,
        config=ClientConfig(
            retry=RetryPolicy.safe(max_attempts=2),
            auth_retries=0,
        ),
    )
    async with client:
        result = await SearchApi(client).search()

    assert result == Reply(ok=True)
    assert mapper_inputs == [{}, {}]
    assert requests == [
        {"page": 1, "attempt": 1},
        {"page": 1, "attempt": 2},
    ]


@pytest.mark.parametrize(
    ("target", "build"),
    [
        (DataclassMutableWire, DataclassMutableWire),
        (PydanticMutableWire, PydanticMutableWire),
        (MsgspecMutableWire, MsgspecMutableWire),
        (cast(type[object], MutableSource), lambda **values: values),
    ],
)
async def test_projection_gets_a_fresh_mutable_source_copy_for_each_target_family(
    target: type[object],
    build: Any,
) -> None:
    caller_values = ["original"]
    mapper_inputs: list[list[str]] = []
    requests: list[dict[str, object]] = []

    def project(source: MutableSource) -> object:
        mapper_inputs.append(source["values"].copy())
        source["values"].append("projected")
        return build(values=source["values"])

    projection = BodyProjection(
        MutableSource,
        target,
        project,
        JsonBody(),
    )

    class MutationApi(AsyncApi):
        @api.put("/mutate", body=projection, responses=RESPONSES)
        async def mutate(self, **request: Unpack[MutableSource]) -> Reply:
            raise NotImplementedError

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(cast(dict[str, object], json.loads(request.content)))
        status = 503 if len(requests) == 1 else 200
        return httpx.Response(status, json={"ok": True})

    client = _client(
        handler,
        config=ClientConfig(
            retry=RetryPolicy.safe(max_attempts=2),
            auth_retries=0,
        ),
    )
    async with client:
        result = await MutationApi(client).mutate(values=caller_values)

    assert result == Reply(ok=True)
    assert caller_values == ["original"]
    assert mapper_inputs == [["original"], ["original"]]
    assert requests == [
        {"values": ["original", "projected"]},
        {"values": ["original", "projected"]},
    ]
