from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Annotated, Any, cast

import pytest
from zapros import AsyncBaseHandler, BaseHandler, Request, Response

from eazy_sdk import AsyncClient, Client, Inject, SyncApi, api
from eazy_sdk._internal import CompiledContract, InputField, RequestLocation, compile_endpoint
from eazy_sdk._internal.errors import PlanError
from eazy_sdk.clients.sync_client import _UNSET, _raw_call
from eazy_sdk.request import Header, Query
from eazy_sdk.response import Bytes, Responses, Success


@dataclass(frozen=True)
class Contract:
    operation_id: str
    input_fields: tuple[InputField, ...]
    method: str = "GET"
    path: str = "/items"
    responses: object = None


def test_compiler_rejects_duplicate_query_wire_names() -> None:
    fields = (
        InputField("first", "tag", str, True, RequestLocation.QUERY, Query("tag")),
        InputField("second", "tag", str, True, RequestLocation.QUERY, Query("tag")),
    )

    with pytest.raises(PlanError, match="duplicate query input wire name 'tag'"):
        compile_endpoint(Contract("duplicate-query", fields))


def test_compiler_rejects_repeated_query_expansion() -> None:
    field = InputField(
        "tags",
        "tag",
        list[str],
        True,
        RequestLocation.QUERY,
        Query("tag", explode=True),
    )

    with pytest.raises(PlanError, match="would repeat wire name"):
        compile_endpoint(Contract("repeated-query", (field,)))


def test_compiler_accepts_explicit_single_value_collection_encoding() -> None:
    field = InputField(
        "tags",
        "tag",
        list[str],
        True,
        RequestLocation.QUERY,
        Query("tag", explode=False),
    )

    compiled: CompiledContract[object] = compile_endpoint(Contract("csv-query", (field,)))

    assert compiled.input_fields == (field,)


@pytest.mark.parametrize(
    ("url", "params"),
    [
        ("https://example.test/items?tag=a&tag=b", None),
        ("https://example.test/items?tag=a", {"tag": "b"}),
        ("https://example.test/items", {"tag": ["a", "b"]}),
    ],
)
def test_raw_client_rejects_duplicate_or_repeating_query_before_execution(
    url: str, params: dict[str, object] | None
) -> None:
    with pytest.raises(ValueError, match=r"duplicate raw query|single-value codec"):
        _raw_call("GET", url, params, None, None, _UNSET, _UNSET)


class ClosingHandler(BaseHandler):
    def __init__(self) -> None:
        self.closed = False
        self.requests: list[Request] = []

    def handle(self, request: Request) -> Response:
        self.requests.append(request)
        return Response(
            200,
            [("Content-Type", "application/json")],
            content=b'{"ok":true}',
            request=request,
        )

    def close(self) -> None:
        self.closed = True


class AsyncClosingHandler(AsyncBaseHandler):
    def __init__(self) -> None:
        self.closed = False

    async def ahandle(self, request: Request) -> Response:
        return Response(200, content=b"{}", request=request)

    async def aclose(self) -> None:
        self.closed = True


def test_target_sync_client_accepts_and_owns_arbitrary_zapros_handler() -> None:
    handler = ClosingHandler()
    client = Client(base_url="https://example.test", handler=handler)
    client.close()

    assert handler.closed


def test_client_rejects_reuse_of_handler_it_already_closed() -> None:
    handler = ClosingHandler()
    Client(base_url="https://example.test", handler=handler).close()

    with pytest.raises(RuntimeError, match="handler was already closed"):
        Client(base_url="https://example.test", handler=handler)


async def test_target_async_client_can_borrow_arbitrary_zapros_handler() -> None:
    handler = AsyncClosingHandler()
    client = AsyncClient(
        base_url="https://example.test",
        handler=handler,
        owns_handler=False,
    )
    await client.aclose()

    assert not handler.closed


def test_public_client_uses_zapros_high_level_json_without_mutating_input() -> None:
    handler = ClosingHandler()
    payload = {"z": 1, "a": {"nested": [1, 2]}}

    with Client(base_url="https://example.test", handler=handler) as client:
        response = client.post("/items", json=payload)

    assert response.status_code == 200
    assert handler.requests[0].body == b'{"z":1,"a":{"nested":[1,2]}}'
    assert payload == {"z": 1, "a": {"nested": [1, 2]}}


def test_target_client_constructor_is_transport_agnostic() -> None:
    module = importlib.import_module("eazy_sdk")
    client_type = cast(Any, module.__dict__["Client"])

    parameters = inspect.signature(client_type).parameters

    assert {"base_url", "handler", "config", "owns_handler", "profile"} <= parameters.keys()
    assert "raw" not in parameters


def _device_id() -> str:
    return "device-42"


class InjectApi(SyncApi):
    @api.get(
        "/injected",
        operation_id="phase18.inject",
        responses=Responses[bytes](success=(Success(200, Bytes()),)),
        inject=(
            Inject(Header("X-Device-ID"), _device_id),
            Inject(Query("timestamp"), "1700000000"),
        ),
    )
    def injected(self) -> bytes:
        raise NotImplementedError


def test_inject_uses_dependency_slots_without_changing_public_signature() -> None:
    handler = ClosingHandler()

    with Client(base_url="https://example.test", handler=handler) as client:
        api = InjectApi(client)
        public_parameters = inspect.signature(cast(Any, api.injected)).parameters
        result = api.injected()

    assert result == b'{"ok":true}'
    assert not public_parameters
    request = handler.requests[0]
    assert request.headers["X-Device-ID"] == "device-42"
    assert str(request.url) == "https://example.test/injected?timestamp=1700000000"


class CollidingInjectApi(SyncApi):
    @api.get(
        "/collision",
        operation_id="phase18.inject-collision",
        responses=Responses[bytes](success=()),
        inject=(Inject(Query("value"), "injected"),),
    )
    def collision(self, *, value: Annotated[str, Query("value")]) -> bytes:
        raise NotImplementedError


def test_inject_collision_is_a_compile_error() -> None:
    with pytest.raises(PlanError, match="injected query wire name collides"):
        cast(Any, CollidingInjectApi.collision).resolve(CollidingInjectApi.defaults).compile()
