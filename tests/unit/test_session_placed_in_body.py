"""A session value that travels with the request's own parameters.

Some sites carry the session in the query of a read and in the form of a write. The session
model says so once, ``Placed.body(...) | Placed.query(...)``, and operations stay unaware.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from zapros import Request, Response

from eazy_sdk import (
    Http,
    HttpOperation,
    Identity,
    Inject,
    JsonField,
    Query,
    SyncApi,
    SyncRoot,
    api_group,
    op,
)
from eazy_sdk.auth import AuthContext, Placed, session_scheme
from eazy_sdk.core.errors import PlanError
from eazy_sdk.request import FieldOrder, Form, Wire, markers
from eazy_sdk.testing import RecordingHandler

BASE = "https://mail.example.test"


@dataclass(frozen=True, slots=True)
class MailSession:
    email: Annotated[str, Placed.body("email", secret=False) | Placed.query("email", secret=False)]
    token: Annotated[str, Placed.body("token") | Placed.query("token")]


@dataclass(frozen=True, slots=True)
class BodyOnlySession:
    token: Annotated[str, Placed.body("token")]


SESSION = session_scheme(MailSession, name="mail")
BODY_ONLY = session_scheme(BodyOnlySession, name="body-only")


class _Service:
    def __init__(self) -> None:
        self.refreshed = 0

    async def acquire(self, credentials: object, context: AuthContext[Any]) -> MailSession:
        return MailSession("me@example.test", "first")

    async def refresh(self, session: MailSession, context: AuthContext[Any]) -> MailSession:
        self.refreshed += 1
        return MailSession(session.email, "second")


Answer = dict[str, bool]


@dataclass(frozen=True, slots=True, kw_only=True)
class ListThreads(HttpOperation[Answer]):
    __http__ = Http.get("/threads")

    folder: Query[int] = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoveThreads(HttpOperation[Answer]):
    # Removing the same threads twice is the same as once: the request may be sent again.
    __http__ = Http.post("/threads/remove", idempotent=True)

    ids: Form[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoveInBrowserOrder(HttpOperation[Answer]):
    """The order one site's own client uses: the session around a technical flag."""

    __http__ = Http.post(
        "/threads/remove",
        inject=(Inject(placement=markers.Form("htmlencoded"), source="false"),),
        wire=Wire(order=FieldOrder(body=("ids", "email", "htmlencoded", "token"))),
    )

    ids: Form[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class Rename(HttpOperation[Answer]):
    __http__ = Http.post("/folders/rename")

    name: JsonField[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class Ping(HttpOperation[Answer]):
    """A write with no body of its own: the session falls back to the query."""

    __http__ = Http.post("/ping")


class Mail(SyncApi):
    security = SESSION

    list = op(ListThreads)
    remove = op(RemoveThreads)
    remove_ordered = op(RemoveInBrowserOrder)
    rename = op(Rename)
    ping = op(Ping)


class Sdk(SyncRoot):
    mail = api_group(Mail)


class BodyOnlyMail(SyncApi):
    security = BODY_ONLY

    list = op(ListThreads)


class BodyOnlySdk(SyncRoot):
    mail = api_group(BodyOnlyMail)


def _ok(request: Request) -> Response:
    return Response(
        200, [("Content-Type", "application/json")], content=b'{"ok": true}', request=request
    )


def _open(handler: RecordingHandler, service: _Service | None = None) -> Sdk:
    auth = SESSION.configure(
        session=MailSession("me@example.test", "first"), service=service or _Service()
    )
    return Sdk.from_handler(handler=handler, base_url=BASE, identity=Identity(auth=(auth,)))


def _query(request: Request) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(str(request.url)).query)


def _form(request: Request) -> list[tuple[str, str]]:
    assert isinstance(request.body, bytes)
    return parse_qsl(request.body.decode())


def test_a_read_carries_the_session_in_the_query() -> None:
    handler = RecordingHandler(_ok)
    with _open(handler) as sdk:
        sdk.mail.list(folder=3)
    assert _query(handler.last_request) == [
        ("folder", "3"),
        ("email", "me@example.test"),
        ("token", "first"),
    ]
    assert not handler.last_request.body


def test_a_form_write_carries_it_in_the_form_and_not_in_the_query() -> None:
    handler = RecordingHandler(_ok)
    with _open(handler) as sdk:
        sdk.mail.remove(ids="7")
    assert _query(handler.last_request) == []
    assert _form(handler.last_request) == [
        ("ids", "7"),
        ("email", "me@example.test"),
        ("token", "first"),
    ]


def test_the_order_of_the_form_is_the_declared_one() -> None:
    handler = RecordingHandler(_ok)
    with _open(handler) as sdk:
        sdk.mail.remove_ordered(ids="7")
    assert _form(handler.last_request) == [
        ("ids", "7"),
        ("email", "me@example.test"),
        ("htmlencoded", "false"),
        ("token", "first"),
    ]


def test_a_json_write_carries_it_as_keys_of_the_document() -> None:
    handler = RecordingHandler(_ok)
    with _open(handler) as sdk:
        sdk.mail.rename(name="Archive")
    assert _query(handler.last_request) == []
    assert isinstance(handler.last_request.body, bytes)
    assert json.loads(handler.last_request.body) == {
        "name": "Archive",
        "email": "me@example.test",
        "token": "first",
    }


def test_a_write_without_a_body_falls_back_to_the_query() -> None:
    handler = RecordingHandler(_ok)
    with _open(handler) as sdk:
        sdk.mail.ping()
    assert _query(handler.last_request) == [("email", "me@example.test"), ("token", "first")]


def test_a_stale_session_is_refreshed_and_the_form_is_sent_again_with_the_new_value() -> None:
    answers = iter([401, 200])

    def respond(request: Request) -> Response:
        status = next(answers)
        return Response(
            status, [("Content-Type", "application/json")], content=b'{"ok": true}', request=request
        )

    handler = RecordingHandler(respond)
    service = _Service()
    with _open(handler, service) as sdk:
        assert sdk.mail.remove(ids="7") == {"ok": True}
    assert service.refreshed == 1
    assert [dict(_form(request))["token"] for request in handler.requests] == ["first", "second"]


def test_a_body_only_place_is_refused_for_an_operation_without_a_body() -> None:
    handler = RecordingHandler(_ok)
    auth = BODY_ONLY.configure(session=BodyOnlySession("first"), service=_Service())  # type: ignore[arg-type]
    with (
        BodyOnlySdk.from_handler(
            handler=handler, base_url=BASE, identity=Identity(auth=(auth,))
        ) as sdk,
        pytest.raises(PlanError, match="declares no flat body fields"),
    ):
        sdk.mail.list()
    handler.assert_count(0)


def test_a_chain_is_read_left_to_right_and_keeps_each_place() -> None:
    chain = (Placed.body("t") | Placed.query("t") | Placed.header("X-T", prefix="T ")).chain
    assert [(item.location, item.name, item.prefix) for item in chain] == [
        ("body", "t", ""),
        ("query", "t", ""),
        ("header", "X-T", "T "),
    ]
    assert all(item.otherwise == () for item in chain)
