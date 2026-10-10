"""Phase 59.1 and 59.2: ``OneOf`` and ``Not`` tags, and ``Payload[T]`` in a generic model.

The scenario is a mail service that answers HTTP 200 for everything and says what happened in a
``status`` field of its envelope: 200 is the result, 401 and 403 are a rejected token, anything
else is a refusal with details. The three are told apart by the models alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import pydantic
import pytest

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import (
    AmbiguousResponseError,
    ApiError,
    Const,
    Not,
    OneOf,
    Payload,
)
from eazy_sdk.response.markers import Tag, tags_of

BASE = "https://mail.example"


def _serve(payload: object) -> Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )

    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True))


class Account(pydantic.BaseModel):
    email: str = ""


class Ok[T](pydantic.BaseModel):
    status: Annotated[int, Const(200)]
    body: Payload[T]


class TokenRefused(pydantic.BaseModel):
    status: Annotated[int, OneOf(401, 403)]
    body: Any = None


class Refused(pydantic.BaseModel):
    status: Annotated[int, Not(200)]
    body: Any = None


class TokenRejected(ApiError[TokenRefused]):
    pass


class MailRefused(ApiError[Refused]):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class GetAccount(HttpOperation[Account]):
    __http__ = Http.get(
        "/api/v1/user/short",
        success={200: Ok[Account]},
        errors={200: TokenRejected},
        fallback=MailRefused,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ListAccounts(HttpOperation[list[Account]]):
    __http__ = Http.get("/api/v1/users", success={200: Ok[list[Account]]})


class Mail(SyncApi):
    account = op(GetAccount)
    accounts = op(ListAccounts)


# --- 59.1: OneOf and Not -------------------------------------------------------------------------


def test_one_of_and_not_are_read_as_tags_with_their_own_wording() -> None:
    assert tags_of(TokenRefused) == (Tag("status", 401, "in", (401, 403)),)
    assert tags_of(Refused) == (Tag("status", 200, "!="),)
    assert repr(tags_of(TokenRefused)[0]) == "<status in (401, 403)>"
    assert repr(tags_of(Refused)[0]) == "<status != 200>"


@pytest.mark.parametrize(
    ("body", "one_of", "not_"),
    [
        ({"status": 401}, True, True),
        ({"status": 403}, True, True),
        ({"status": 200}, False, False),
        ({"status": 500}, False, True),
        ({"status": "401"}, False, True),
        ({}, False, False),
    ],
)
def test_tags_compare_values_together_with_their_types(
    body: dict[str, object], one_of: bool, not_: bool
) -> None:
    assert tags_of(TokenRefused)[0].holds(body) is one_of
    assert tags_of(Refused)[0].holds(body) is not_


def test_true_is_not_one() -> None:
    tag = Tag("ok", 1, "in", (1, 2))
    assert tag.holds({"ok": 1})
    assert not tag.holds({"ok": True})
    assert Tag("ok", 1, "!=").holds({"ok": True})


def test_the_envelope_is_split_three_ways_without_a_lambda() -> None:
    with _serve({"status": 200, "body": {"email": "u@mail.example"}}) as client:
        assert Mail(client).account().email == "u@mail.example"

    with _serve({"status": 401, "body": "token"}) as client, pytest.raises(TokenRejected) as token:
        Mail(client).account()
    assert token.value.error.status == 401

    refusal = {"status": 400, "body": {"sort": "invalid"}}
    with _serve(refusal) as client, pytest.raises(MailRefused) as refused:
        Mail(client).account()
    assert refused.value.error.body == {"sort": "invalid"}


def test_two_tags_that_both_hold_stay_ambiguous() -> None:
    """Nothing in the declarations orders ``Not(200)`` against ``OneOf(401, 403)``."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Both(HttpOperation[Account]):
        __http__ = Http.get(
            "/both", success={200: Ok[Account]}, errors={200: [TokenRejected, MailRefused]}
        )

    class Api(SyncApi):
        both = op(Both)

    with _serve({"status": 401}) as client, pytest.raises(AmbiguousResponseError) as raised:
        Api(client).both()
    assert "TokenRefused" in str(raised.value) and "Refused" in str(raised.value)


def test_a_value_the_field_cannot_hold_is_refused_at_declaration() -> None:
    class WrongOneOf(pydantic.BaseModel):
        status: Annotated[int, OneOf(401, "403")]

    class WrongNot(pydantic.BaseModel):
        status: Annotated[int, Not("200")]

    with pytest.raises(PlanError, match=r"OneOf\(401, '403'\) on WrongOneOf.status"):
        tags_of(WrongOneOf)
    with pytest.raises(PlanError, match=r"Not\('200'\) on WrongNot.status"):
        tags_of(WrongNot)


def test_a_marker_that_is_not_a_constant_is_refused() -> None:
    with pytest.raises(PlanError, match="names no constant"):
        OneOf()
    with pytest.raises(PlanError, match="not a constant"):
        OneOf(401, [403])  # type: ignore[arg-type]
    with pytest.raises(PlanError, match="not a constant"):
        Not({})  # type: ignore[arg-type]


def test_a_field_states_one_tag() -> None:
    class Twice(pydantic.BaseModel):
        status: Annotated[int, Not(200), OneOf(401, 403)]

    with pytest.raises(PlanError, match="declares 2 constants"):
        tags_of(Twice)


def test_one_of_is_a_value() -> None:
    assert OneOf(1, 2) == OneOf(1, 2)
    assert OneOf(1, 2) != OneOf(2, 1)
    assert len({OneOf(1, 2), OneOf(1, 2)}) == 1
    with pytest.raises(AttributeError):
        OneOf(1).values = (2,)


# --- 59.2: Payload[T] in a generic model ---------------------------------------------------------


def test_a_generic_envelope_returns_its_parameter() -> None:
    with _serve({"status": 200, "body": [{"email": "a@x"}, {"email": "b@x"}]}) as client:
        accounts = Mail(client).accounts()
    assert [item.email for item in accounts] == ["a@x", "b@x"]


def test_a_generic_envelope_used_without_a_parameter_is_refused() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Bare(HttpOperation[Account]):
        __http__ = Http.get("/bare", success={200: Ok})

    class Api(SyncApi):
        bare = op(Bare)

    unbound = r"Ok\.body is Payload\[T\].*write Ok\[Result\]"
    with _serve({"status": 200, "body": {}}) as client, pytest.raises(PlanError, match=unbound):
        Api(client).bare()


def test_a_parameter_that_disagrees_with_the_operation_is_still_refused() -> None:
    class Other(pydantic.BaseModel):
        id: str = ""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Wrong(HttpOperation[Account]):
        __http__ = Http.get("/wrong", success={200: Ok[Other]})

    class Api(SyncApi):
        wrong = op(Wrong)

    disagrees = "Payload is Other, the operation returns Account"
    with _serve({"status": 200, "body": {}}) as client, pytest.raises(PlanError, match=disagrees):
        Api(client).wrong()
