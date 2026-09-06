"""Phase 50.3: the request is a value — build it, change it, send it, without I/O.

``request()`` constructs the operation class and touches nothing else; ``evolve`` copies it
through the model library's own function; ``send`` turns the value into exactly the bytes a
direct call would have produced. What holds the three together is that they are the same
class the operation is declared as, so a value from one operation cannot be sent by another.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict, cast

import msgspec
import pytest
from pydantic import BaseModel, ConfigDict
from zapros import BaseHandler, Request, Response

from eazy_sdk import Client, Http, HttpOperation, Path, Query, SyncApi, api, op
from eazy_sdk.models import ModelAdapterError, default_model_adapters
from eazy_sdk.policies import CallOptions
from eazy_sdk.request import markers

BASE = "https://api.example"


class Order(BaseModel):
    id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GetOrder(HttpOperation[Order]):
    __http__ = Http.get("/orders/{order_id}")

    order_id: Path[str]
    expand: Annotated[tuple[str, ...], markers.Query(explode=False)] = ()
    locale: Query[str] = "en"


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchOrders(HttpOperation[Order]):
    __http__ = Http.get("/orders")

    term: Query[str]


class PydanticOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    page: int = 1


class MsgspecOrder(msgspec.Struct, frozen=True, kw_only=True):
    order_id: str
    page: int = 1


class DictOrder(TypedDict):
    order_id: str
    page: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DataclassOrder:
    order_id: str
    page: int = 1


class _Handler(BaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    @property
    def targets(self) -> list[str]:
        return [str(request.url) for request in self.requests]

    def handle(self, request: Request) -> Response:
        self.requests.append(request)
        return Response(
            200, [("Content-Type", "application/json")], content=b'{"id":"o-1"}', request=request
        )

    def close(self) -> None:
        return None


class Orders(SyncApi):
    get_order = op(GetOrder)
    search = op(SearchOrders)


def _client(handler: BaseHandler | None = None) -> Client:
    return Client(base_url=BASE, handler=handler or _Handler())


# --- 50.3.1 evolve across the four libraries ------------------------------------------


def test_evolve_dataclass_pydantic_msgspec_typeddict() -> None:
    """One call, four libraries, each copied by the function its own library provides."""

    models = default_model_adapters()
    values: tuple[Any, ...] = (
        DataclassOrder(order_id="o-1"),
        PydanticOrder(order_id="o-1"),
        MsgspecOrder(order_id="o-1"),
    )
    for value in values:
        changed = models.evolve(value, page=2)
        assert type(changed) is type(value)
        assert changed is not value
        assert (changed.page, changed.order_id) == (2, "o-1")
        assert value.page == 1, "the value it was copied from is untouched"

    # A TypedDict value is a plain dict, so no adapter recognises it by value; the adapter
    # chosen by type answers the same question.
    typed: DictOrder = {"order_id": "o-1", "page": 1}
    evolved = models.adapter_for_type(DictOrder).evolve(typed, {"page": 2})
    assert evolved == {"order_id": "o-1", "page": 2}
    assert typed["page"] == 1


def test_evolve_unknown_field_names_fields() -> None:
    """A typo reads the same whichever library the operation is declared with."""

    models = default_model_adapters()
    for value in (DataclassOrder(order_id="o-1"), PydanticOrder(order_id="o-1")):
        with pytest.raises(ModelAdapterError) as failure:
            models.evolve(value, pgae=2)
        assert str(failure.value) == (
            f"{type(value).__name__} has no field 'pgae'; fields: order_id, page"
        )


def test_frozen_is_read_from_the_library() -> None:
    """``frozen`` answers per library, and ``None`` where the library has no such notion."""

    models = default_model_adapters()
    assert models.adapter_for_type(DataclassOrder).frozen(DataclassOrder) is True
    assert models.adapter_for_type(PydanticOrder).frozen(PydanticOrder) is True
    assert models.adapter_for_type(MsgspecOrder).frozen(MsgspecOrder) is True
    assert models.adapter_for_type(DictOrder).frozen(DictOrder) is None


# --- 50.3.2 the bound operation's request, send and evolve ----------------------------


def test_request_builds_value_without_io() -> None:
    """``request()`` is the constructor: no handler is touched, and the value is the class."""

    handler = _Handler()
    orders = Orders(_client(handler))
    request = orders.get_order.request(order_id="o-1", expand=("items",))
    assert isinstance(request, GetOrder)
    assert (request.order_id, request.expand, request.locale) == ("o-1", ("items",), "en")
    assert handler.targets == []


def test_send_value_equals_direct_call_bytes() -> None:
    """A value sent and the same arguments called are one request, compared as bytes."""

    handler = _Handler()
    orders = Orders(_client(handler))
    request = orders.get_order.request(order_id="o-1", expand=("items",), locale="ru")
    orders.get_order.send(request)
    orders.get_order(order_id="o-1", expand=("items",), locale="ru")
    sent, direct = handler.requests
    assert _golden(sent) == _golden(direct)


def _golden(request: Request) -> tuple[object, ...]:
    return (
        request.method,
        str(request.url),
        tuple(request.headers.items()),
        request.body,
    )


def test_send_rejects_foreign_operation() -> None:
    """A value belongs to the operation it was declared for, and to no other."""

    orders = Orders(_client())
    foreign = orders.search.request(term="ada")
    with pytest.raises(TypeError) as failure:
        orders.get_order.send(cast(Any, foreign))
    assert str(failure.value) == "send() expects GetOrder, got SearchOrders"


def test_evolve_then_send() -> None:
    """The paging loop without a paging abstraction: one value, evolved per page."""

    handler = _Handler()
    orders = Orders(_client(handler))
    request = orders.get_order.request(order_id="o-1")
    for locale in ("en", "ru", "de"):
        orders.get_order.send(orders.get_order.evolve(request, locale=locale))
    assert [target.rsplit("=", 1)[-1] for target in handler.targets] == ["en", "ru", "de"]
    assert cast(GetOrder, request).locale == "en", "the value the loop started from is untouched"


def test_send_options_for_op_operation() -> None:
    """An ``op()`` operation takes per-call options through ``send``, not the constructor."""

    orders = Orders(_client())
    request = orders.get_order.request(order_id="o-1")
    assert orders.get_order.send(request, options=CallOptions(timeout=5)).id == "o-1"
    with pytest.raises(TypeError):
        # The constructor of an operation class has no room for a per-call option.
        cast(Any, orders.get_order)(order_id="o-1", options=CallOptions(timeout=5))


def test_decorated_operation_keeps_options_parameter() -> None:
    """A decorated operation still takes ``options=`` in the call itself."""

    class Decorated(SyncApi):
        @api.get("/orders/{order_id}")
        def get_order(self, *, order_id: Path[str]) -> Order:
            raise NotImplementedError

    sdk = Decorated(_client())
    # ``options=`` is a runtime parameter of a decorated operation; the checker sees the
    # synthesized constructor, which does not name it (§10, item 29).
    assert cast(Any, sdk.get_order)(order_id="o-1", options=CallOptions(timeout=5)).id == "o-1"


def test_send_accepts_only_the_values_it_declares() -> None:
    """A request value carries every field, so sending it needs nothing else."""

    orders = Orders(_client())
    request = orders.get_order.request(order_id="o-1")
    envelope = orders.get_order.send_with_response(request)
    assert envelope.status_code == 200
    assert envelope.value.id == "o-1"
