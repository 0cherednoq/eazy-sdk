from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict, Unpack, cast

import httpx
import msgspec
import pytest
from pydantic import BaseModel, ConfigDict, Field

from eazy_sdk import AsyncApi, AsyncClient, ClientConfig, api
from eazy_sdk.clients import RetryPolicy
from eazy_sdk.codecs import EncodeContext
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import BodyProjection, BodyProjectionError, JsonBody
from eazy_sdk.response import Json, Responses, Success


class PublicBody(TypedDict):
    value: str


class NestedWire(TypedDict):
    envelope: dict[str, str]
    attempt: int


@dataclass(frozen=True, slots=True)
class Reply:
    ok: bool


RESPONSES = Responses[Reply](success=(Success(200, Json(Reply)),))


async def _execute(
    projection: BodyProjection[Any, Any],
    handler: Any,
    *,
    config: ClientConfig | None = None,
    value: str = "visible",
) -> Reply:
    class ProjectionApi(AsyncApi):
        @api.put("/project", body=projection, responses=RESPONSES)
        async def operation(self, **request: Unpack[PublicBody]) -> Reply:
            raise NotImplementedError

    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=config,
    ) as client:
        return await ProjectionApi(client).operation(value=value)


async def test_projection_builds_exact_nested_json_from_flat_kwargs() -> None:
    seen: list[dict[str, object]] = []

    def to_wire(source: PublicBody) -> NestedWire:
        return {"envelope": {"value": source["value"]}, "attempt": 1}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    result = await _execute(
        BodyProjection(PublicBody, NestedWire, to_wire, JsonBody()),
        handler,
    )

    assert result == Reply(ok=True)
    assert seen == [{"envelope": {"value": "visible"}, "attempt": 1}]


async def test_projection_and_codec_are_fresh_once_per_retry_attempt() -> None:
    projections: list[int] = []
    documents: list[object] = []
    captures: list[dict[str, object]] = []

    @dataclass(frozen=True, slots=True)
    class ExactJsonCodec:
        captured: list[object]
        name: str = "phase21-exact-json"
        media_type: str = "application/vnd.phase21+json"

        def encode(self, value: object, context: EncodeContext) -> bytes:
            self.captured.append(copy.deepcopy(value))
            return json.dumps(value, separators=(",", ":")).encode()

    def to_wire(source: PublicBody) -> NestedWire:
        attempt = len(projections) + 1
        projections.append(attempt)
        return {"envelope": {"value": source["value"]}, "attempt": attempt}

    async def handler(request: httpx.Request) -> httpx.Response:
        captures.append(cast(dict[str, object], json.loads(request.content)))
        status = 503 if len(captures) == 1 else 200
        return httpx.Response(status, json={"ok": True})

    codec = ExactJsonCodec(documents)
    result = await _execute(
        BodyProjection(PublicBody, NestedWire, to_wire, codec),
        handler,
        config=ClientConfig(
            retry=RetryPolicy.safe(max_attempts=2),
            auth_retries=0,
        ),
    )

    assert result.ok
    assert projections == [1, 2]
    assert documents == captures == [
        {"envelope": {"value": "visible"}, "attempt": 1},
        {"envelope": {"value": "visible"}, "attempt": 2},
    ]


async def test_projection_is_fresh_on_managed_redirect() -> None:
    projection_calls = 0
    captures: list[tuple[str, int]] = []

    def to_wire(source: PublicBody) -> NestedWire:
        nonlocal projection_calls
        projection_calls += 1
        return {
            "envelope": {"value": source["value"]},
            "attempt": projection_calls,
        }

    async def handler(request: httpx.Request) -> httpx.Response:
        document = cast(dict[str, object], json.loads(request.content))
        captures.append((request.url.path, cast(int, document["attempt"])))
        if request.url.path == "/project":
            return httpx.Response(307, headers={"location": "/redirected"})
        return httpx.Response(200, json={"ok": True})

    await _execute(
        BodyProjection(PublicBody, NestedWire, to_wire, JsonBody()),
        handler,
        config=ClientConfig(auth_retries=0, max_redirects=1),
    )

    assert projection_calls == 2
    assert captures == [("/project", 1), ("/redirected", 2)]


async def test_standard_json_and_custom_codec_receive_the_same_semantic_document() -> None:
    standard: list[object] = []
    custom: list[object] = []

    @dataclass(frozen=True, slots=True)
    class CaptureCodec:
        name: str = "capture-json"
        media_type: str = "application/json"

        def encode(self, value: object, context: EncodeContext) -> bytes:
            custom.append(copy.deepcopy(value))
            return json.dumps(value, separators=(",", ":")).encode()

    def to_wire(source: PublicBody) -> NestedWire:
        return {"envelope": {"value": source["value"]}, "attempt": 7}

    async def standard_handler(request: httpx.Request) -> httpx.Response:
        standard.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    async def custom_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    await _execute(
        BodyProjection(PublicBody, NestedWire, to_wire, JsonBody()),
        standard_handler,
    )
    await _execute(
        BodyProjection(PublicBody, NestedWire, to_wire, CaptureCodec()),
        custom_handler,
    )

    assert standard == custom == [
        {"envelope": {"value": "visible"}, "attempt": 7}
    ]


@dataclass
class DataclassTarget:
    value: str
    marker: int = 2


class PydanticTarget(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    value: str = Field(serialization_alias="wireValue")
    marker: int = 2


class MsgspecTarget(msgspec.Struct, rename={"value": "wireValue"}):
    value: str
    marker: int = 2


class CountingTarget(BaseModel):
    dumps: ClassVar[int] = 0

    value: str

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        type(self).dumps += 1
        return super().model_dump(*args, **kwargs)


@pytest.mark.parametrize(
    ("target", "mapper", "expected"),
    [
        (
            DataclassTarget,
            lambda source: DataclassTarget(source["value"]),
            {"value": "visible", "marker": 2},
        ),
        (
            PydanticTarget,
            lambda source: PydanticTarget(value=source["value"]),
            {"wireValue": "visible", "marker": 2},
        ),
        (
            MsgspecTarget,
            lambda source: MsgspecTarget(source["value"]),
            {"wireValue": "visible", "marker": 2},
        ),
    ],
)
async def test_projection_target_adapter_preserves_order_aliases_and_defaults(
    target: type[object],
    mapper: Any,
    expected: dict[str, object],
) -> None:
    captures: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captures.append(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    projection = BodyProjection(
        PublicBody,
        target,
        mapper,
        JsonBody(),
    )
    await _execute(projection, handler)

    assert captures == [expected]
    assert list(captures[0]) == list(expected)


async def test_projection_target_model_is_dumped_once_per_attempt() -> None:
    CountingTarget.dumps = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    await _execute(
        BodyProjection(
            PublicBody,
            CountingTarget,
            lambda source: CountingTarget(value=source["value"]),
            JsonBody(),
        ),
        handler,
    )

    assert CountingTarget.dumps == 1


class ListSource(TypedDict):
    values: list[str]


class ListWire(TypedDict):
    values: list[str]


async def test_projection_mapper_cannot_mutate_caller_collections() -> None:
    caller_values = ["original"]

    def mutate(source: ListSource) -> ListWire:
        source["values"].append("projected")
        return {"values": source["values"]}

    projection = BodyProjection(ListSource, ListWire, mutate, JsonBody())

    class ProjectionApi(AsyncApi):
        @api.put("/project", body=projection, responses=RESPONSES)
        async def operation(self, **request: Unpack[ListSource]) -> Reply:
            raise NotImplementedError

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"values": ["original", "projected"]}
        return httpx.Response(200, json={"ok": True})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={}, cookies={})
    async with AsyncClient(
        base_url="https://api.example.test",
        handler=AsyncHttpxHandler(raw, owns_client=True),
    ) as client:
        await ProjectionApi(client).operation(values=caller_values)

    assert caller_values == ["original"]


class AccountWire(BaseModel):
    login: str


class ValidatedWire(BaseModel):
    account: AccountWire


@pytest.mark.parametrize("failure", ["mapper", "target"])
async def test_projection_failures_are_safe_and_target_error_keeps_nested_path(
    failure: str,
) -> None:
    secret = "do-not-leak"

    def mapper(source: PublicBody) -> ValidatedWire:
        if failure == "mapper":
            raise ValueError(secret)
        return cast(ValidatedWire, {"account": {}})

    projection = BodyProjection(PublicBody, ValidatedWire, mapper, JsonBody())
    sends = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(BodyProjectionError) as captured:
        await _execute(projection, handler, value=secret)

    message = str(captured.value)
    assert secret not in message
    assert captured.value.__cause__ is None
    assert sends == 0
    if failure == "target":
        assert "account.login" in message
