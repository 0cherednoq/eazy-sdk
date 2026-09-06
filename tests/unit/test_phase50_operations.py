"""Phase 50.1: operation classes, short markers, ``op()``, decorator synthesis, ``Omittable``.

Every invariant test starts with its number from the plan (I1..I13); every diagnostic test
asserts a substring of the text fixed in the plan's §4 tables (D-01..D-13).
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar, TypedDict, cast, get_type_hints

import httpx
import msgspec
import pytest
from pydantic import BaseModel, ConfigDict, Field
from zapros import BaseHandler, Request, Response

import eazy_sdk
from eazy_sdk import (
    UNSET,
    AsyncApi,
    Client,
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
from eazy_sdk import request as request_api
from eazy_sdk.compile.input import inspect_operation_input
from eazy_sdk.core.errors import PlanError
from eazy_sdk.core.http import RequestLocation
from eazy_sdk.dependencies import Injected, dependency
from eazy_sdk.models import default_model_adapters
from eazy_sdk.operation import _HttpSpec
from eazy_sdk.preparation import PreparedCall
from eazy_sdk.request import BodyProjection, Wire, markers, short
from eazy_sdk.response import ApiError, Json
from tests._support.zapros_clients import client_from_httpx


@dataclass(frozen=True)
class User:
    name: str


class Problem(BaseModel):
    code: str


class UserNotFound(ApiError[Problem]):
    pass


class _Handler(BaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def handle(self, request: Request) -> Response:
        self.requests.append(request)
        return Response(
            200,
            [("Content-Type", "application/json")],
            content=b'{"name":"Ada"}',
            request=request,
        )

    def close(self) -> None:
        return None


def _client() -> Client:
    return Client(base_url="https://api.example", handler=_Handler())


def _prepared(call: PreparedCall) -> tuple[str, str, tuple[tuple[str, str], ...], object]:
    return (
        call.method,
        call.url,
        tuple(sorted((item.name, item.value) for item in call.headers)),
        call.body,
    )


# --- markers -------------------------------------------------------------------------


def test_short_marker_equals_annotated_form() -> None:
    """I4: ``Query[int]`` and ``Annotated[int, markers.Query()]`` resolve to one annotation."""

    class Fields:
        short_form: request_api.Query[int]
        long_form: Annotated[int, markers.Query()]
        root_form: eazy_sdk.Query[int]

    hints = get_type_hints(Fields, include_extras=True)
    assert hints["short_form"] == hints["long_form"] == hints["root_form"]
    assert hints["short_form"].__metadata__ == (markers.Query(),)
    for name in short.__dict__:
        if name.startswith("_") or name in {"Annotated", "TypeVar", "markers"}:
            continue
        alias = getattr(short, name)
        assert getattr(request_api, name) is alias
        if name in eazy_sdk.__all__:
            assert getattr(eazy_sdk, name) is alias


def test_markers_module_reexports_every_descriptor() -> None:
    """I4: ``markers`` is the qualified home of every placement descriptor, nothing more."""

    from eazy_sdk.request import descriptors, params

    expected = {
        "Path": params.Path,
        "Query": params.Query,
        "QueryString": params.QueryString,
        "Header": params.Header,
        "Cookie": params.Cookie,
        "JsonField": descriptors.JsonField,
        "Form": descriptors.Form,
        "Part": descriptors.Part,
        "JsonBody": descriptors.JsonBody,
        "FormBody": descriptors.FormBody,
        "MultipartBody": descriptors.MultipartBody,
        "BytesBody": descriptors.BytesBody,
        "ReplayableStreamBody": descriptors.ReplayableStreamBody,
        "BodyProjection": descriptors.BodyProjection,
    }
    assert set(markers.__all__) == set(expected)
    assert markers.__all__ == sorted(markers.__all__)
    for name, descriptor in expected.items():
        assert getattr(markers, name) is descriptor
    # The short form has no name argument, so the descriptors that require one have no alias.
    assert not hasattr(short, "QueryString")
    assert not hasattr(short, "BodyProjection")
    # The root never exposes the descriptor classes under the short names.
    assert eazy_sdk.Path is not params.Path
    assert request_api.Query is not params.Query


# --- Http / _HttpSpec ------------------------------------------------------------------


def test_http_verbs_build_one_spec() -> None:
    """I2: every verb, the decorator included, builds the same ``_HttpSpec`` record."""

    spec = Http.get("/users/{user_id}", operation_id="getUser", errors={404: UserNotFound})
    assert isinstance(spec, _HttpSpec)
    assert (spec.method, spec.path, spec.operation_id) == ("GET", "/users/{user_id}", "getUser")
    assert Http.request("get", "/x") == Http.get("/x")
    for verb in ("delete", "get", "head", "options", "patch", "post", "put", "trace"):
        assert getattr(Http, verb)("/x").method == verb.upper()
    # The decorator's verb namespace goes through the same constructor.
    decorator = api.post("/x", tags=("a",), idempotent=True)
    assert decorator.spec == Http.post("/x", tags=("a",), idempotent=True)
    with pytest.raises(ValueError, match="invalid HTTP method token"):
        Http.request("BAD METHOD", "/x")


def test_spec_rejects_unknown_option() -> None:
    """One keyword set by construction: an option the record does not know is a TypeError."""

    with pytest.raises(TypeError, match="responses"):
        cast(Any, Http.get)("/x", responses=object())
    with pytest.raises(TypeError, match="response"):
        cast(Any, api.get)("/x", response=Json())


# --- reading fields ------------------------------------------------------------------


def _dataclass_search() -> type[HttpOperation[User]]:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class SearchOrders(HttpOperation[User]):
        __http__ = Http.get("/orders")
        page: Query[int] = 1
        per_page: Annotated[int, markers.Query("perPage")] = 20
        locale: Annotated[str, markers.Header("Accept-Language")] = "en"

    return SearchOrders


def _pydantic_search() -> type[HttpOperation[User]]:
    class SearchOrders(BaseModel, HttpOperation[User]):
        model_config = ConfigDict(frozen=True, serialize_by_alias=True)
        __http__ = Http.get("/orders")
        page: Query[int] = 1
        per_page: Query[int] = Field(20, serialization_alias="perPage")
        locale: Annotated[str, markers.Header()] = Field(
            "en", serialization_alias="Accept-Language"
        )

    return SearchOrders


def _msgspec_search() -> type[HttpOperation[User]]:
    class SearchOrders(msgspec.Struct, HttpOperation[User], frozen=True, kw_only=True):
        __http__ = Http.get("/orders")
        page: Query[int] = 1
        per_page: Query[int] = msgspec.field(default=20, name="perPage")
        locale: Annotated[str, markers.Header()] = msgspec.field(
            default="en", name="Accept-Language"
        )

    return SearchOrders


LIBRARIES = pytest.mark.parametrize(
    "factory",
    [_dataclass_search, _pydantic_search, _msgspec_search],
    ids=["dataclass", "pydantic", "msgspec"],
)


@LIBRARIES
def test_field_reading_dataclass_pydantic_msgspec_same_schema(
    factory: Callable[[], type[HttpOperation[User]]],
) -> None:
    """I3: three libraries, one wire naming rule, one compiled schema."""

    schema = inspect_operation_input(
        factory(), operation_id="search", path="/orders", models=default_model_adapters()
    )
    assert schema.operation_type is not None
    assert tuple(
        (item.python_name, item.wire_name, item.location, item.required, item.omittable)
        for item in schema.fields
    ) == (
        ("page", "page", RequestLocation.QUERY, False, False),
        ("per_page", "perPage", RequestLocation.QUERY, False, False),
        ("locale", "Accept-Language", RequestLocation.HEADER, False, False),
    )


def test_wire_name_from_model_rename() -> None:
    """I3: msgspec ``rename`` owns every wire name; the marker only says where."""

    class Camel(msgspec.Struct, HttpOperation[User], frozen=True, kw_only=True, rename="camel"):
        __http__ = Http.post("/orders")
        order_id: JsonField[str]
        side_name: Query[str] = "x"

    schema = inspect_operation_input(
        Camel, operation_id="camel", path="/orders", models=default_model_adapters()
    )
    assert [item.wire_name for item in schema.fields] == ["orderId", "sideName"]


def test_wire_name_from_marker_for_dataclass() -> None:
    """I3: a dataclass has no alias, so the marker is the one place a name can be written."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[User]):
        __http__ = Http.get("/x")
        order_id: Annotated[str, markers.Query("orderId")]
        plain: Query[str] = "p"

    schema = inspect_operation_input(
        Op, operation_id="op", path="/x", models=default_model_adapters()
    )
    assert [item.wire_name for item in schema.fields] == ["orderId", "plain"]


def test_wire_name_twice_is_rejected() -> None:
    """D-10: model and marker both naming the wire field is a declaration error."""

    class Twice(msgspec.Struct, HttpOperation[User], frozen=True, kw_only=True):
        __http__ = Http.get("/x")
        per_page: Annotated[int, markers.Query("perPage")] = msgspec.field(
            default=1, name="PerPage"
        )

    with pytest.raises(PlanError) as captured:
        inspect_operation_input(
            Twice, operation_id="SearchOrders", path="/x", models=default_model_adapters()
        )
    assert (
        "input field 'per_page' in 'SearchOrders' names its wire field twice: "
        "model says 'PerPage', marker says 'perPage'; keep one"
    ) in str(captured.value)


def test_pydantic_alias_without_serialize_by_alias_rejected() -> None:
    """D-11: a Pydantic alias that never reaches the wire is refused, not silently ignored."""

    class Silent(BaseModel, HttpOperation[User]):
        model_config = ConfigDict(frozen=True)
        __http__ = Http.get("/x")
        per_page: Query[int] = Field(1, alias="perPage")

    with pytest.raises(PlanError) as captured:
        inspect_operation_input(
            Silent, operation_id="SearchOrders", path="/x", models=default_model_adapters()
        )
    assert (
        "input field 'per_page' in 'SearchOrders' has alias 'perPage' that will not reach the "
        "wire; set model_config = ConfigDict(serialize_by_alias=True)"
    ) in str(captured.value)


def test_not_frozen_rejected() -> None:
    """D-02 / I5: an operation value is immutable, whichever library declares it."""

    @dataclass(slots=True, kw_only=True)
    class Mutable(HttpOperation[User]):
        __http__ = Http.get("/x")
        page: Query[int] = 1

    class Api(SyncApi):
        search = op(Mutable)

    with pytest.raises(PlanError) as captured:
        Api.search.declaration  # noqa: B018 - evaluated for its diagnostics
    assert (
        "operation class Mutable must be frozen: use @dataclass(frozen=True) / "
        "msgspec.Struct(frozen=True) / ConfigDict(frozen=True)"
    ) in str(captured.value)


def test_pydantic_base_order_rejected() -> None:
    """D-03: ``BaseModel`` must come before the operation base."""

    with warnings.catch_warnings():
        # Pydantic itself warns about this order; the SDK turns it into a declaration error.
        warnings.simplefilter("ignore")

        class Wrong(HttpOperation[User], BaseModel):
            model_config = ConfigDict(frozen=True)
            __http__ = Http.get("/x")
            page: Query[int] = 1

    with pytest.raises(PlanError) as captured:
        inspect_operation_input(
            Wrong, operation_id="wrong", path="/x", models=default_model_adapters()
        )
    assert (
        "operation class Wrong must list BaseModel before HttpOperation: "
        "class Wrong(BaseModel, HttpOperation[...])"
    ) in str(captured.value)


def test_missing_http_rejected() -> None:
    """D-01: an operation class without ``__http__`` is refused where it is published."""

    @dataclass(frozen=True, slots=True)
    class GetOrder(HttpOperation[User]):
        order_id: Path[str]

    with pytest.raises(PlanError) as captured:
        op(GetOrder)
    assert (
        "operation class GetOrder has no __http__; assign Http.get(...) or another verb"
    ) in str(captured.value)


def test_unsupported_model_and_missing_result_type_rejected() -> None:
    """D-04 / D-05: a TypedDict is not an operation, and a result type must come from somewhere."""

    class Dict(TypedDict):
        page: Query[int]

    with pytest.raises(PlanError, match="is not a model any configured adapter supports"):
        inspect_operation_input(
            cast(Any, Dict), operation_id="d", path="/x", models=default_model_adapters()
        )

    @dataclass(frozen=True, slots=True)
    class NoResult(HttpOperation):  # type: ignore[type-arg]
        __http__ = Http.get("/x")

    class Api(SyncApi):
        no_result = op(NoResult)

    with pytest.raises(PlanError) as captured:
        Api.no_result.declaration  # noqa: B018 - evaluated for its diagnostics
    assert "operation class NoResult declares neither HttpOperation[T] nor success=" in str(
        captured.value
    )


def test_omittable_without_default_rejected() -> None:
    """D-12: ``Omittable`` describes a value that may be left out, so it needs ``UNSET``."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[User]):
        __http__ = Http.get("/x")
        page: Query[Omittable[int]]

    with pytest.raises(PlanError) as captured:
        inspect_operation_input(
            Op, operation_id="Op", path="/x", models=default_model_adapters()
        )
    assert "input field 'page' in 'Op' is Omittable but has no default; give it UNSET" in str(
        captured.value
    )


def test_unpack_is_rejected_with_hint() -> None:
    """D-13: the removed ``Unpack`` form names the class form as the way out."""

    with pytest.raises(PlanError) as captured:

        class Api(SyncApi):
            @api.get("/users")  # type: ignore[type-var, arg-type]
            def get_user(self, **request: object) -> User:
                raise NotImplementedError

    assert (
        "operation 'get_user' uses **request: Unpack[...], which 0.3.0 removed; declare an "
        "operation class and publish it with op(...)"
    ) in str(captured.value)


def test_init_false_field_is_serialized_but_not_a_parameter() -> None:
    """I1: a constant lives on the class as ``init=False`` and still reaches the wire."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Ping(HttpOperation[User]):
        __http__ = Http.post("/ping")
        message: JsonField[str]
        platform: JsonField[str] = field(init=False, default="android")

    class Api(SyncApi):
        ping = op(Ping)

    assert "platform" not in inspect.signature(Ping).parameters
    prepared = Api(_client()).ping.prepare(message="hi")
    assert prepared.body == (("message", "hi"), ("platform", "android"))


# --- op() and the descriptor -------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class GetOrder(HttpOperation[User]):
    __http__ = Http.get("/orders/{order_id}", errors={404: UserNotFound})
    order_id: Path[str]
    expand: Annotated[tuple[str, ...], markers.Query(explode=False)] = ()
    page: Query[Omittable[int]] = UNSET
    note: Annotated[Omittable[str], markers.JsonField("note")] = UNSET


class Orders(SyncApi):
    get_order = op(GetOrder)


class AsyncOrders(AsyncApi):
    get_order = op(GetOrder)


def test_op_on_sync_and_async_router() -> None:
    """I1 / I12: one class is published on either router; the router picks the call style."""

    assert Orders.get_order.asynchronous is False
    assert AsyncOrders.get_order.asynchronous is True
    assert Orders.get_order.Operation is GetOrder
    bound = Orders(_client()).get_order
    assert bound.Operation is GetOrder
    assert tuple(inspect.signature(bound).parameters) == ("order_id", "expand", "page", "note")
    assert bound(order_id="42") == User(name="Ada")
    assert inspect.iscoroutinefunction(AsyncOrders(cast(Any, _client())).get_order.__call__)


def test_op_rejects_non_operation() -> None:
    """``op()`` takes an operation class and nothing else."""

    with pytest.raises(TypeError, match="op\\(\\) expects an operation class, got"):
        op(cast(Any, object()))
    with pytest.raises(TypeError, match="expects a subclass of HttpOperation"):
        op(cast(Any, User))


def test_omittable_field_is_dropped() -> None:
    """I5 / I1: ``UNSET`` never reaches the executor; an explicit ``None`` does."""

    orders = Orders(_client())
    values, _ = Orders.get_order._bind_arguments(orders, order_id="1")
    assert values == {"order_id": "1", "expand": ()}
    values, _ = Orders.get_order._bind_arguments(orders, order_id="1", page=3, note="n")
    assert values == {"order_id": "1", "expand": (), "page": 3, "note": "n"}


def test_unset_and_omitted_argument_prepare_same_bytes() -> None:
    """I5: ``prepare(page=UNSET)`` and ``prepare()`` are one and the same request."""

    orders = Orders(_client())
    assert _prepared(orders.get_order.prepare(order_id="1", page=UNSET)) == _prepared(
        orders.get_order.prepare(order_id="1")
    )
    assert orders.get_order.prepare(order_id="1", note=UNSET).body is None


def test_operation_id_defaults_to_qualname() -> None:
    """The class name is the operation id unless ``__http__`` says otherwise."""

    assert Orders.get_order.operation_id == "GetOrder"
    assert Orders.get_order.declaration.operation_id == "GetOrder"

    @dataclass(frozen=True, slots=True)
    class Named(HttpOperation[User]):
        __http__ = Http.get("/x", operation_id="custom")

    assert op(Named).operation_id == "custom"


def test_duplicate_operation_id_names_both_classes() -> None:
    """D-06: a duplicate id is reported with both declaring classes."""

    @dataclass(frozen=True, slots=True)
    class First(HttpOperation[User]):
        __http__ = Http.get("/a", operation_id="same")

    @dataclass(frozen=True, slots=True)
    class Second(HttpOperation[User]):
        __http__ = Http.get("/b", operation_id="same")

    with pytest.raises(TypeError) as captured:

        class Api(SyncApi):
            first = op(First)
            second = op(Second)

    message = str(captured.value)
    assert "duplicate operation_id: same" in message
    assert "First" in message and "Second" in message


# --- the decorator synthesizes the same class ----------------------------------------


class Users(SyncApi):
    @api.get("/users/{user_id}", errors={404: UserNotFound})
    def get_user(self, *, user_id: Path[int], locale: Query[str] = "en") -> User:
        """Fetch one user."""
        raise NotImplementedError

    @api.get("/users")
    def list_users(self, *, page: Query[int] | None = None) -> User:
        raise NotImplementedError


def test_decorator_synthesizes_public_operation_class() -> None:
    """I2: ``Users.get_user.Operation`` is a frozen slotted dataclass with ``__http__``."""

    operation = Users.get_user.Operation
    assert operation.__qualname__ == "Users.get_user.Operation"
    assert operation.__doc__ == "Fetch one user."
    assert dataclasses.is_dataclass(operation)
    params = operation.__dataclass_params__
    assert params.frozen and params.kw_only
    assert operation.__slots__ == ("user_id", "locale")
    assert operation.__http__ == Http.get(
        "/users/{user_id}", operation_id="get_user", errors={404: UserNotFound}
    )
    assert issubclass(operation, HttpOperation)
    assert Users.get_user.declaration.operation_type is operation
    request = operation(user_id=1)
    assert (request.user_id, request.locale) == (1, "en")
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.user_id = 2


def test_decorator_none_default_becomes_unset() -> None:
    """A ``None`` default keeps meaning "not passed, not sent": it becomes ``UNSET``."""

    hints = get_type_hints(Users.list_users.Operation, include_extras=True)
    assert hints["page"] == Omittable[Query[int] | None]
    assert cast(Any, Users.list_users.Operation()).page is UNSET
    users = Users(_client())
    assert users.list_users.prepare().url == "https://api.example/users"
    assert users.list_users.prepare(page=2).url == "https://api.example/users?page=2"


def test_responses_keyword_is_gone() -> None:
    """``responses=`` and ``response=`` no longer exist; the mapping is the one way."""

    with pytest.raises(TypeError, match="responses"):
        cast(Any, api.get)("/x", responses=object())
    assert "responses" not in inspect.signature(Http.get).parameters
    assert not hasattr(api, "_singular")


# --- projection on the declaration ----------------------------------------------------


class Wire_(TypedDict):
    device: str
    payload: dict[str, object]


DEVICE = dependency(str, name="device", provide=lambda: "dev-1")


def test_projection_source_defaults_to_operation() -> None:
    """I1: with no ``source`` the projection receives the operation value itself."""

    seen: list[object] = []

    def project(source: object) -> Wire_:
        seen.append(source)
        return {"device": "static", "payload": {"x": cast(Any, source).x}}

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Create(HttpOperation[User]):
        __http__ = Http.post(
            "/create", projection=BodyProjection(Wire_, project, markers.JsonBody())
        )
        x: int

    class Api(SyncApi):
        create = op(Create)

    prepared = Api(_client()).create.prepare(x=7)
    assert Api.create.declaration.projection is Create.__http__.projection
    assert isinstance(seen[0], Create) and seen[0].x == 7
    assert prepared.body == (("device", "static"), ("payload", (("x", 7),)))


def test_projection_receives_injected() -> None:
    """A two-argument projection gets the resolved ``requires=`` values as ``Injected``."""

    def project(source: object, injected: Injected) -> Wire_:
        return {"device": cast(str, injected[DEVICE.dependency]), "payload": {}}

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Create(HttpOperation[User]):
        __http__ = Http.post(
            "/create",
            requires=(DEVICE,),
            projection=BodyProjection(Wire_, project, markers.JsonBody()),
        )
        x: int = 1

    class Api(SyncApi):
        create = op(Create)

    bodies: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"name": "Ada"})

    client = client_from_httpx(
        httpx.Client(base_url="https://api.example", transport=httpx.MockTransport(handler))
    )
    assert Api(client).create() == User(name="Ada")
    assert bodies == [{"device": "dev-1", "payload": {}}]


def test_wire_projection_field_is_gone() -> None:
    """The projection is a representation, not a byte contract: ``Wire`` no longer carries it."""

    assert "projection" not in {item.name for item in dataclasses.fields(Wire)}
    with pytest.raises(TypeError):
        Wire(projection=None)  # type: ignore[call-arg]
    spec = Http.post("/x", projection=BodyProjection(Wire_, lambda s: s, markers.JsonBody()))
    assert spec.projection is not None and spec.wire.encrypted is None


def test_class_var_http_is_not_a_field() -> None:
    """``__http__`` and other ``ClassVar`` attributes never become request fields."""

    @dataclass(frozen=True, slots=True)
    class Op(HttpOperation[User]):
        __http__ = Http.get("/x")
        version: ClassVar[str] = "1"

    assert [item.python_name for item in op(Op).declaration.input_fields] == []
