"""Review of phase 50: what the declaration already answers is not re-asked per request.

The projection's arity was read with ``inspect.signature`` on every body build and on every
retry, although it is a property of ``BodyProjection.using`` and never changes. It is decided
once, by the compiler, and travels on the compiled contract.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, cast

import httpx

from eazy_sdk import Http, HttpOperation, SyncApi, op
from eazy_sdk.clients import _decisions
from eazy_sdk.compile import compile_endpoint
from eazy_sdk.compile.http_compiler import CompiledContract
from eazy_sdk.dependencies import Injected, dependency
from eazy_sdk.request import BodyProjection, markers
from tests._support.zapros_clients import client_from_httpx


@dataclass(frozen=True)
class User:
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Wire:
    device: str
    value: int


DEVICE = dependency(str, name="device", provide=lambda: "dev-1")


def _one(source: object) -> Wire:
    return Wire(device="static", value=cast(Any, source).value)


def _two(source: object, injected: Injected) -> Wire:
    return Wire(device=cast(str, injected[DEVICE.dependency]), value=cast(Any, source).value)


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateOne(HttpOperation[User]):
    __http__ = Http.post("/one", projection=BodyProjection(Wire, _one, markers.JsonBody()))

    value: int = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateTwo(HttpOperation[User]):
    __http__ = Http.post(
        "/two",
        requires=(DEVICE,),
        projection=BodyProjection(Wire, _two, markers.JsonBody()),
    )

    value: int = 2


class Api(SyncApi):
    one = op(CreateOne)
    two = op(CreateTwo)


def _client(bodies: list[object]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"name": "Ada"})

    return client_from_httpx(
        httpx.Client(base_url="https://api.example", transport=httpx.MockTransport(handler))
    )


def test_both_projection_forms_still_receive_what_they_declare() -> None:
    """One parameter reads the operation; two also read the resolved dependencies."""

    bodies: list[object] = []
    sdk = Api(_client(bodies))
    assert sdk.one(value=7) == User(name="Ada")
    assert sdk.two(value=8) == User(name="Ada")
    assert bodies == [
        {"device": "static", "value": 7},
        {"device": "dev-1", "value": 8},
    ]


def test_arity_travels_on_the_compiled_contract() -> None:
    """The compiler answers it once; the request path reads the answer."""

    one: CompiledContract[Any] = compile_endpoint(Api.one.declaration)
    two: CompiledContract[Any] = compile_endpoint(Api.two.declaration)

    assert (one.projection_arity, two.projection_arity) == (1, 2)


def test_the_request_path_does_not_read_signatures() -> None:
    """``_decisions`` builds every request body, and no longer needs ``inspect`` at all."""

    source = inspect.getsource(_decisions)
    assert "inspect" not in source


def test_decisions_module_holds_no_mutable_default() -> None:
    """A shared mutable default is one edit away from being a shared mutable state."""

    source = inspect.getsource(_decisions)
    assert "= {}," not in source
    assert "_NO_INJECTIONS" in source
