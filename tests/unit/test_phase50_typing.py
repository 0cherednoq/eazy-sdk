"""Phase 50 typing fixtures: what the checker sees is the constructor's own signature.

The positive source must type-check under ``mypy --strict`` and ``basedpyright``; the negative
source must be rejected by both for the reasons named in the assertions.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


POSITIVE = r'''# pyright: strict, reportUnknownVariableType=false, reportUnusedCallResult=false
# pyright: reportUnsafeMultipleInheritance=false, reportUnannotatedClassAttribute=false
from dataclasses import dataclass, field
from typing import Annotated, Any, assert_type

import msgspec
from pydantic import BaseModel, ConfigDict

from eazy_sdk import (
    UNSET,
    AsyncApi,
    Http,
    HttpOperation,
    JsonField,
    Omittable,
    Path,
    Query,
    SyncApi,
    api,
    op,
)
from eazy_sdk.preparation import PreparedCall
from eazy_sdk.request import markers
from eazy_sdk.response import ResponseEnvelope


@dataclass(frozen=True)
class Order:
    id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetOrder(HttpOperation[Order]):
    __http__ = Http.get("/orders/{order_id}")

    order_id: Path[str]
    expand: Query[tuple[str, ...]] = ()
    locale: Annotated[str, markers.Header("Accept-Language")] = "en"
    page: Query[Omittable[int]] = UNSET
    tags: JsonField[list[str]] = field(default_factory=list)


class SearchOrders(BaseModel, HttpOperation[Order]):
    model_config = ConfigDict(frozen=True, serialize_by_alias=True)
    __http__ = Http.get("/orders")

    term: Query[str]
    page: Query[int] = 1


class CountOrders(msgspec.Struct, HttpOperation[int], frozen=True, kw_only=True):
    __http__ = Http.get("/orders/count")

    term: Query[str]
    page: Query[int] = 1


class Orders(SyncApi):
    get_order = op(GetOrder)
    search = op(SearchOrders)
    count = op(CountOrders)

    @api.get("/orders/{order_id}/legacy")
    def legacy(self, *, order_id: Path[str], locale: Query[str] = "en") -> Order:
        raise NotImplementedError


class AsyncOrders(AsyncApi):
    get_order = op(GetOrder)


def takes_str(value: str) -> None:
    del value


takes_str(GetOrder(order_id="1").order_id)  # the alias is transparent to the checker
assert_type(GetOrder(order_id="1").expand, tuple[str, ...])
assert_type(GetOrder(order_id="1").page, Omittable[int])


def value_proof(orders: Orders) -> None:
    """The request is a value: built, copied and sent, all typed by the operation."""

    request = orders.get_order.request(order_id="1")
    assert_type(request, HttpOperation[Order])
    assert_type(orders.get_order.evolve(request, expand=("items",)), HttpOperation[Order])
    assert_type(orders.get_order.send(request), Order)
    assert_type(orders.get_order.send_with_response(request), ResponseEnvelope[Order, Any])
    orders.search.send(orders.search.request(term="x"))


def sync_proof(orders: Orders) -> None:
    assert_type(orders.get_order(order_id="1"), Order)
    assert_type(orders.get_order(order_id="1", expand=("items",), page=2), Order)
    assert_type(orders.search(term="x"), Order)
    assert_type(orders.count(term="x", page=3), int)
    assert_type(orders.legacy(order_id="1"), Order)
    assert_type(orders.get_order.with_response(order_id="1"), ResponseEnvelope[Order, Any])
    assert_type(orders.get_order.prepare(order_id="1"), PreparedCall)
    assert_type(orders.get_order.request(order_id="1"), HttpOperation[Order])
    assert_type(Orders.get_order.Operation, type[HttpOperation[Order]])


async def async_proof(orders: AsyncOrders) -> None:
    assert_type(await orders.get_order(order_id="1"), Order)
    assert_type(await orders.get_order.with_response(order_id="1"), ResponseEnvelope[Order, Any])
'''


NEGATIVE = r'''# pyright: strict
from dataclasses import dataclass

from eazy_sdk import UNSET, Http, HttpOperation, Omittable, Path, Query, SyncApi, op


@dataclass(frozen=True)
class Order:
    id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetOrder(HttpOperation[Order]):
    __http__ = Http.get("/orders/{order_id}")

    order_id: Path[str]
    page: Query[Omittable[int]] = UNSET
    locale: Query[str] = "en"


class Orders(SyncApi):
    get_order = op(GetOrder)


def invalid(orders: Orders) -> None:
    orders.get_order(order_id=1)  # wrong argument type
    orders.get_order(order_id="1", unknown=2)  # unknown keyword
    GetOrder(order_id="1", page="x")  # Omittable[int] does not accept str
    GetOrder(order_id="1", locale=UNSET)  # UNSET needs Omittable
    orders.get_order.send(orders.get_order.request(order_id=1))  # wrong argument type again
'''


def _run(checker: str, source: Path) -> subprocess.CompletedProcess[str]:
    command = (
        [sys.executable, "-m", "mypy", "--strict", str(source)]
        if checker == "mypy"
        else [sys.executable, "-m", "basedpyright", "--pythonversion", "3.13", str(source)]
    )
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_phase50_operation_signatures_are_visible(checker: str) -> None:
    """I1 / I4: ``op()`` shows the constructor with defaults on three libraries."""

    with tempfile.TemporaryDirectory(prefix="phase50-typing-", dir=ROOT / "tests") as temp:
        source = Path(temp) / "positive.py"
        source.write_text(POSITIVE, encoding="utf-8")
        result = _run(checker, source)
    assert result.returncode == 0 or "0 errors" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_phase50_typing_rejects_bad_calls(checker: str) -> None:
    """I5: ``UNSET`` is not ``Any`` — it is accepted only where ``Omittable`` says so."""

    with tempfile.TemporaryDirectory(prefix="phase50-typing-", dir=ROOT / "tests") as temp:
        source = Path(temp) / "negative.py"
        source.write_text(NEGATIVE, encoding="utf-8")
        result = _run(checker, source)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "unknown" in output
    assert output.count("negative.py") >= 5, output
