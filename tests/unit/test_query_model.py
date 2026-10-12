"""The query of a request declared as one private model.

The caller passes what it owns. Everything else the service demands in the query (flags,
versions, a timestamp) is a field of the model with a default, typed, and absent from the
public signature.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from pydantic import BaseModel, ConfigDict, Field
from zapros import Request, Response

from eazy_sdk import (
    Http,
    HttpOperation,
    Identity,
    Inject,
    Query,
    SyncApi,
    SyncRoot,
    api_group,
    op,
)
from eazy_sdk.auth import AuthContext, Placed, session_scheme
from eazy_sdk.codecs import ScalarEncodeContext
from eazy_sdk.core.errors import PlanError
from eazy_sdk.dependencies import DependencyContext, DependencyRegistry, Injected, dependency
from eazy_sdk.exceptions import EazySdkError
from eazy_sdk.request import FieldOrder, Form, QueryModel, Wire, markers
from eazy_sdk.testing import RecordingHandler

BASE = "https://mail.example.test"
Answer = dict[str, bool]
_ticks = iter(range(1000, 2000))


def _tick() -> int:
    return next(_ticks)


class View(Enum):
    LIST = "list"
    GRID = "grid"


class Sort(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = "date"
    order: str = "desc"


@dataclass(frozen=True, slots=True)
class _Json:
    name: str = "json"

    def encode(self, value: object, context: ScalarEncodeContext) -> str:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        return json.dumps(value, separators=(",", ":"))


class _ThreadQuery(BaseModel):
    """The query as the site's own client sends it: the order of the fields is the wire order."""

    # The pydantic adapter takes the alias as the wire name only when the model serializes by it.
    model_config = ConfigDict(serialize_by_alias=True)

    quotes_version: int = 1
    id: str
    offset: int = 0
    force_custom_thread: bool = False
    use_color_scheme: int = 1
    view: View = View.LIST
    sort: Annotated[Sort, markers.Query(codec=_Json())] = Sort()
    search: str | None = None
    stamp: int = Field(default_factory=_tick, alias="_")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetThread(HttpOperation[Answer]):
    __http__ = Http.get("/threads/thread", query=QueryModel(_ThreadQuery))

    id: str
    offset: int = 0


class _Session(BaseModel):
    token: Annotated[str, Placed.query("token")]


SESSION = session_scheme(_Session, name="mail")


class _Service:
    async def acquire(self, credentials: object, context: AuthContext[Any]) -> _Session:
        return _Session(token="first")

    async def refresh(self, session: _Session, context: AuthContext[Any]) -> _Session:
        return _Session(token="second")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtectedThread(HttpOperation[Answer]):
    __http__ = Http.get(
        "/threads/thread",
        query=QueryModel(_ThreadQuery),
        inject=(Inject(placement=markers.Header("X-Request-Id"), source="r-1"),),
    )

    id: str


@dataclass(frozen=True, slots=True)
class Device:
    device_id: str


DEVICE = dependency(Device, name="device")


@dataclass(frozen=True, slots=True)
class _OneDevice:
    def resolve(self, context: DependencyContext) -> Device:
        return Device("device-7")


class _SignedQuery(BaseModel):
    login: str
    username: str
    device: str
    check: str


def _signed(call: Lookup, injected: Injected) -> _SignedQuery:
    device = injected[DEVICE.dependency]
    assert isinstance(device, Device)
    return _SignedQuery(
        login=call.login,
        username=call.login,
        device=device.device_id,
        check=f"{call.login}:{device.device_id}",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Lookup(HttpOperation[Answer]):
    """One value under two names, a value the application knows, and one computed from both."""

    __http__ = Http.get(
        "/lookup", requires=(DEVICE,), query=QueryModel(_SignedQuery, using=_signed)
    )

    login: str


class _MoveQuery(BaseModel):
    folder: int
    confirm: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class Move(HttpOperation[Answer]):
    """A query model beside an ordinary declared parameter and a form body."""

    __http__ = Http.post("/threads/move", query=QueryModel(_MoveQuery))

    folder: int
    dry: Query[bool] = False
    ids: Form[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class Reordered(HttpOperation[Answer]):
    __http__ = Http.get(
        "/threads/thread",
        query=QueryModel(_MoveQuery),
        wire=Wire(order=FieldOrder(query=("confirm", "folder"))),
    )

    folder: int


class Mail(SyncApi):
    get = op(GetThread)
    lookup = op(Lookup)
    move = op(Move)
    reordered = op(Reordered)


class Protected(SyncApi):
    security = SESSION

    get = op(ProtectedThread)


class Sdk(SyncRoot):
    mail = api_group(Mail)
    protected = api_group(Protected)


def _ok(request: Request) -> Response:
    return Response(
        200, [("Content-Type", "application/json")], content=b'{"ok": true}', request=request
    )


def _open(handler: RecordingHandler, identity: Identity | None = None) -> Sdk:
    return Sdk.from_handler(handler=handler, base_url=BASE, identity=identity or Identity())


def _query(request: Request) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(str(request.url)).query)


def test_the_callers_signature_has_only_what_the_caller_owns() -> None:
    assert list(inspect.signature(GetThread).parameters) == ["id", "offset"]


def test_every_field_of_the_model_is_one_parameter_in_the_models_order() -> None:
    handler = RecordingHandler(_ok)
    with _open(handler) as sdk:
        sdk.mail.get(id="t-1", offset=20)
    sent = _query(handler.last_request)
    assert sent[:-1] == [
        ("quotes_version", "1"),
        ("id", "t-1"),
        ("offset", "20"),
        ("force_custom_thread", "false"),
        ("use_color_scheme", "1"),
        ("view", "list"),
        ("sort", '{"type":"date","order":"desc"}'),
    ]
    # ``search`` is None and is not sent; the factory field goes out under its wire name.
    assert sent[-1][0] == "_"
    assert sent[-1][1].isdigit()


def test_a_factory_field_is_fresh_on_every_attempt() -> None:
    answers = iter([401, 200])

    def respond(request: Request) -> Response:
        return Response(
            next(answers),
            [("Content-Type", "application/json")],
            content=b'{"ok": true}',
            request=request,
        )

    handler = RecordingHandler(respond)
    auth = SESSION.configure(session=_Session(token="first"), service=_Service())
    with _open(handler, Identity(auth=(auth,))) as sdk:
        assert sdk.protected.get(id="t-1") == {"ok": True}
    first, second = (dict(_query(request)) for request in handler.requests)
    assert int(second["_"]) > int(first["_"])
    assert (first["token"], second["token"]) == ("first", "second")


def test_the_session_and_an_inject_live_beside_the_model() -> None:
    handler = RecordingHandler(_ok)
    auth = SESSION.configure(session=_Session(token="first"), service=_Service())
    with _open(handler, Identity(auth=(auth,))) as sdk:
        sdk.protected.get(id="t-1")
    names = [name for name, _ in _query(handler.last_request)]
    # The model first, in its order; the session after it.
    assert names[0] == "quotes_version"
    assert names[-1] == "token"
    assert handler.last_request.headers["X-Request-Id"] == "r-1"


def test_using_builds_the_model_from_the_call_and_the_injected_values() -> None:
    registry = DependencyRegistry()
    registry.register(DEVICE.dependency, _OneDevice())
    handler = RecordingHandler(_ok)
    with _open(handler, Identity(dependencies=registry)) as sdk:
        sdk.mail.lookup(login="ada")
    assert _query(handler.last_request) == [
        ("login", "ada"),
        ("username", "ada"),
        ("device", "device-7"),
        ("check", "ada:device-7"),
    ]


def test_a_model_lives_beside_a_declared_parameter_and_a_form_body() -> None:
    handler = RecordingHandler(_ok)
    with _open(handler) as sdk:
        sdk.mail.move(folder=7, dry=True, ids="a,b")
    assert _query(handler.last_request) == [("folder", "7"), ("confirm", "true"), ("dry", "true")]
    assert handler.last_request.body == b"ids=a%2Cb"


def test_the_declared_order_overrides_the_models() -> None:
    handler = RecordingHandler(_ok)
    with _open(handler) as sdk:
        sdk.mail.reordered(folder=7)
    assert _query(handler.last_request) == [("confirm", "true"), ("folder", "7")]


# --- refusals, all before a request is sent ----------------------------------------------


def _sdk_of(operation: type[HttpOperation[Any]], *, security: object = None) -> type[SyncRoot]:
    namespace: dict[str, object] = {"call": op(operation)}
    if security is not None:
        namespace["security"] = security
    api = type("Api", (SyncApi,), namespace)
    return type("Root", (SyncRoot,), {"api": api_group(api)})


def _refused(operation: type[HttpOperation[Any]], match: str, **arguments: object) -> None:
    handler = RecordingHandler(_ok)
    # Some of these are caught when the router is assembled, the rest on the first call.
    with pytest.raises(PlanError, match=match):
        root = _sdk_of(operation)
        with root.from_handler(handler=handler, base_url=BASE) as sdk:
            sdk.api.call(**arguments)  # type: ignore[attr-defined]
    handler.assert_count(0)


def test_a_structured_field_without_a_codec_is_refused() -> None:
    class _Bad(BaseModel):
        sort: Sort = Sort()

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[Answer]):
        __http__ = Http.get("/x", query=QueryModel(_Bad))

    _refused(Op, "is not a scalar")


def test_a_field_nothing_feeds_is_refused() -> None:
    class _Needs(BaseModel):
        id: str

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[Answer]):
        __http__ = Http.get("/x", query=QueryModel(_Needs))

    _refused(Op, "has no default and no operation field")


def test_an_operation_field_the_model_does_not_have_is_refused() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[Answer]):
        __http__ = Http.get("/x", query=QueryModel(_MoveQuery))

        folder: int
        stray: str = ""

    _refused(Op, "is not a field of the query model", folder=1)


def test_a_name_declared_twice_is_refused() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[Answer]):
        __http__ = Http.get("/x", query=QueryModel(_MoveQuery))

        folder: int
        confirm: Query[bool] = True

    _refused(Op, "duplicate query input wire name 'confirm'", folder=1)


def test_an_inject_under_a_models_name_is_refused() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[Answer]):
        __http__ = Http.get(
            "/x",
            query=QueryModel(_MoveQuery),
            inject=(Inject(placement=markers.Query("confirm"), source="1"),),
        )

        folder: int

    _refused(Op, "collides with another slot: 'confirm'", folder=1)


def test_a_session_value_under_a_models_name_is_refused() -> None:
    class _Clash(BaseModel):
        token: str = "mine"

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[Answer]):
        __http__ = Http.get("/x", query=QueryModel(_Clash))

    handler = RecordingHandler(_ok)
    root = _sdk_of(Op, security=SESSION)
    auth = SESSION.configure(session=_Session(token="first"), service=_Service())
    with (
        root.from_handler(handler=handler, base_url=BASE, identity=Identity(auth=(auth,))) as sdk,
        pytest.raises(PlanError, match="the session places 'token' in the query"),
    ):
        sdk.api.call()  # type: ignore[attr-defined]
    handler.assert_count(0)


def test_a_model_that_cannot_be_built_fails_the_call_not_the_program() -> None:
    def broken(call: object) -> _MoveQuery:
        raise RuntimeError("no")

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Op(HttpOperation[Answer]):
        __http__ = Http.get("/x", query=QueryModel(_MoveQuery, using=broken))

        folder: int = field(default=1)

    handler = RecordingHandler(_ok)
    with (
        _sdk_of(Op).from_handler(handler=handler, base_url=BASE) as sdk,
        pytest.raises(EazySdkError),
    ):
        sdk.api.call()  # type: ignore[attr-defined]
    handler.assert_count(0)
