"""Phase 54: the fallback, and a constant on a field as the criterion.

The scenario is the one Slack's own specification is written in: 174 methods answer 200 with
either ``{"ok": true, …}`` or ``{"ok": false, "error": …}``, and its schema tells the two apart
with ``enum: [true]`` against ``enum: [false]`` on one field. 54.1 makes the fallback reachable
for a response every declared case declined; 54.2 lets the models state the difference themselves,
with no lambda and no ``when=`` anywhere in the declaration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

import httpx
import msgspec
import pydantic
import pytest

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import (
    ApiError,
    Const,
    Error,
    Json,
    MalformedResponseError,
    NormalizedResponse,
    ResponseContext,
    Responses,
    Success,
)
from eazy_sdk.response.cases import (
    ErrorOutcome,
    SuccessOutcome,
    UnexpectedOutcome,
    _specificity,
)
from eazy_sdk.response.markers import Tag, tags_of

BASE = "https://slack.example"


class Ok(msgspec.Struct):
    ok: bool
    channel: str = ""


class Failure(msgspec.Struct):
    ok: bool
    error: str = ""


class SlackFailed(ApiError[Failure]):
    """The failure half of the envelope, declared once as the operation's fallback."""


def _serve(payload: object, status: int = 200) -> Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )

    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True))


def _context(payload: object, status: int = 200) -> ResponseContext[object]:
    response: NormalizedResponse[object] = NormalizedResponse(
        status,
        f"{BASE}/api/chat.postMessage",
        "POST",
        (("Content-Type", "application/json"),),
        json.dumps(payload).encode(),
    )
    return ResponseContext(response)


@dataclass(frozen=True, slots=True, kw_only=True)
class PostMessage(HttpOperation[Ok]):
    __http__ = Http.post(
        "/api/chat.postMessage",
        success={200: Json(Ok, accept=lambda value: value.ok)},
        fallback=(Json(Failure), SlackFailed),
    )


class Chat(SyncApi):
    post_message = op(PostMessage)


# --- 54.1: the fallback is reconsidered after parsing ------------------------------------------


def test_fallback_is_used_when_the_criterion_rejects_every_candidate() -> None:
    """The declared failure is raised, where the call used to end as an unexpected response.

    ``errors=`` refuses ``DEFAULT`` — that is what ``fallback=`` is for — so before this there
    was no way to declare this response at all in the dictionary form.
    """

    with (
        _serve({"ok": False, "error": "invalid_auth"}) as client,
        pytest.raises(SlackFailed) as raised,
    ):
        Chat(client).post_message()
    assert raised.value.error == Failure(ok=False, error="invalid_auth")


def test_a_matched_case_still_wins_over_the_fallback() -> None:
    """The fallback is read only when nothing claimed the response."""

    with _serve({"ok": True, "channel": "C1"}) as client:
        assert Chat(client).post_message() == Ok(ok=True, channel="C1")


def test_a_malformed_candidate_keeps_its_outcome_instead_of_the_fallback() -> None:
    """A case that claimed the response and could not read it is not papered over.

    The body has no ``ok`` at all: the success case owns the response and the declaration or the
    body is wrong. Answering with the fallback error here would report an API failure for what is
    a broken response.
    """

    with _serve({"channel": "C1"}) as client, pytest.raises(MalformedResponseError):
        Chat(client).post_message()


def test_the_fallback_is_read_once_when_the_status_already_selected_it() -> None:
    """A status no case declares reaches the fallback before parsing, and only once.

    Reading it twice would put two matches in front of arbitration and turn the plainest
    declaration into an ambiguous response.
    """

    with (
        _serve({"ok": False, "error": "fatal_error"}, status=500) as client,
        pytest.raises(SlackFailed) as raised,
    ):
        Chat(client).post_message()
    assert raised.value.error == Failure(ok=False, error="fatal_error")


# --- 54.2: a constant on a field is the criterion ----------------------------------------------


class TaggedOk(msgspec.Struct):
    """The success half of Slack's envelope, stated the way its own schema states it."""

    ok: Annotated[bool, Const(True)]
    channel: str = ""


class TaggedFailure(msgspec.Struct):
    ok: Annotated[bool, Const(False)]
    error: str = ""


class TaggedSlackFailed(ApiError[TaggedFailure]):
    """The same failure, raised for the declaration that states itself with tags."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TaggedPostMessage(HttpOperation[TaggedOk]):
    """Not one lambda and not one ``when=``: the models say which body is which."""

    __http__ = Http.post(
        "/api/chat.postMessage",
        success={200: Json(TaggedOk)},
        errors={200: (Json(TaggedFailure), TaggedSlackFailed)},
    )


class TaggedChat(SyncApi):
    post_message = op(TaggedPostMessage)


def test_tag_splits_success_and_failure_on_one_status() -> None:
    with _serve({"ok": True, "channel": "C1"}) as client:
        assert TaggedChat(client).post_message() == TaggedOk(ok=True, channel="C1")

    with (
        _serve({"ok": False, "error": "invalid_auth"}) as client,
        pytest.raises(TaggedSlackFailed) as raised,
    ):
        TaggedChat(client).post_message()
    assert raised.value.error == TaggedFailure(ok=False, error="invalid_auth")


def test_tag_is_not_inverted_on_an_error_case() -> None:
    """An error case tagged False claims a False body and nothing else.

    Were the tag inverted the way an envelope's ``succeeds`` is, this error case would claim the
    successful body — which is the whole reason a tag states a fact and never a verdict.
    """

    responses: Responses[TaggedOk] = Responses(
        success=(),
        errors=(Error(200, Json(TaggedFailure), exception=TaggedSlackFailed),),
    )
    assert isinstance(responses.inspect(_context({"ok": False, "error": "x"})), ErrorOutcome)
    assert isinstance(responses.inspect(_context({"ok": True, "channel": "C1"})), UnexpectedOutcome)


def test_all_tags_of_a_model_must_match() -> None:
    class TwoTags(msgspec.Struct):
        ok: Annotated[bool, Const(True)]
        kind: Annotated[str, Const("message")]

    responses: Responses[TwoTags] = Responses(success=(Success(200, Json(TwoTags)),))
    assert isinstance(responses.inspect(_context({"ok": True, "kind": "message"})), SuccessOutcome)
    assert isinstance(responses.inspect(_context({"ok": True, "kind": "file"})), UnexpectedOutcome)


def test_one_does_not_satisfy_a_true_tag() -> None:
    """``1 == True`` in Python, and a body saying 1 is not a body saying true."""

    assert Tag("ok", True).holds(TaggedOk(ok=True))
    assert not Tag("ok", True).holds({"ok": 1})
    assert not Tag("ok", False).holds({"ok": 0})
    assert not Tag("ok", True).holds({})


def test_a_case_with_a_tag_ranks_as_conditional() -> None:
    """The tag is a criterion, so it outranks a plain case exactly as ``when=`` does."""

    class Untagged(msgspec.Struct):
        ok: bool

    assert _specificity(Success(200, Json(TaggedOk)))[0] == 1
    assert _specificity(Success(200, Json(Untagged)))[0] == 0


def test_a_one_value_literal_counts_as_the_same_statement() -> None:
    """``Literal["customer"]`` is ``const`` written in the type; several values are not."""

    class Deleted(msgspec.Struct):
        object: Literal["customer"]
        id: str

    class Either(msgspec.Struct):
        object: Literal["customer", "invoice"]
        id: str

    assert tags_of(Deleted) == (Tag("object", "customer"),)
    assert tags_of(Either) == ()
    assert _specificity(Success(200, Json(Deleted)))[0] == 1


def test_accept_overrides_the_tags() -> None:
    """A model that cannot be edited is still steered from the case."""

    responses: Responses[TaggedOk] = Responses(
        success=(Success(200, Json(TaggedOk, accept=lambda _value: True)),),
    )
    assert isinstance(responses.inspect(_context({"ok": False})), SuccessOutcome)


def test_tags_are_inherited_from_a_base_class() -> None:
    class Wrapper(msgspec.Struct):
        ok: Annotated[bool, Const(True)]

    class Page(Wrapper):
        cursor: str = ""

    assert tags_of(Page) == (Tag("ok", True),)


def test_annotations_are_read_once_per_model() -> None:
    """Resolving annotations is what phase 51 stopped repeating; tags are read the same way."""

    assert tags_of(TaggedOk) is tags_of(TaggedOk)


@dataclass(frozen=True, slots=True)
class DataclassOk:
    ok: Annotated[bool, Const(True)]


@dataclass(frozen=True, slots=True)
class DataclassFailure:
    ok: Annotated[bool, Const(False)]


class PydanticOk(pydantic.BaseModel):
    ok: Annotated[bool, Const(True)]


class PydanticFailure(pydantic.BaseModel):
    ok: Annotated[bool, Const(False)]


class TypedDictOk(TypedDict):
    ok: Annotated[bool, Const(True)]


class TypedDictFailure(TypedDict):
    ok: Annotated[bool, Const(False)]


@pytest.mark.parametrize(
    ("ok_model", "failure_model"),
    [
        (DataclassOk, DataclassFailure),
        (PydanticOk, PydanticFailure),
        (TaggedOk, TaggedFailure),
        (TypedDictOk, TypedDictFailure),
    ],
    ids=["dataclass", "pydantic", "msgspec", "typeddict"],
)
def test_tags_work_on_every_model_backend(ok_model: type, failure_model: type) -> None:
    """The reason the tag is ``Annotated`` and not ``Literal``: all four backends carry it."""

    responses: Responses[object] = Responses(
        success=(Success(200, Json(ok_model)),),
        errors=(Error(200, Json(failure_model), exception=SlackFailed),),
    )
    assert isinstance(responses.inspect(_context({"ok": True})), SuccessOutcome)
    assert isinstance(responses.inspect(_context({"ok": False})), ErrorOutcome)


def test_a_constant_the_field_cannot_hold_is_refused() -> None:
    """D-54-01."""

    class Wrong(msgspec.Struct):
        success: Annotated[str, Const(True)]

    with pytest.raises(PlanError, match=r"Const\(True\) on Wrong\.success: a bool is not a str"):
        tags_of(Wrong)


def test_a_constant_a_schema_cannot_state_is_refused() -> None:
    with pytest.raises(PlanError, match="is not a constant a schema states"):
        Const(object())  # type: ignore[arg-type]


def test_two_constants_on_one_field_are_refused() -> None:
    class Twice(msgspec.Struct):
        ok: Annotated[bool, Const(True), Const(False)]

    with pytest.raises(PlanError, match=r"Twice\.ok declares 2 constants"):
        tags_of(Twice)


def test_a_union_field_accepts_a_constant_of_either_member() -> None:
    class Nullable(msgspec.Struct):
        cursor: Annotated[str | None, Const(None)]

    assert tags_of(Nullable) == (Tag("cursor", None),)


def test_a_broken_tag_is_reported_before_the_request_is_sent() -> None:
    """The check runs in preflight, so the typo is reported with the request still unsent."""

    class Wrong(msgspec.Struct):
        ok: Annotated[int, Const("yes")]

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Broken(HttpOperation[Wrong]):
        __http__ = Http.get("/api/broken", success={200: Json(Wrong)})

    class Service(SyncApi):
        broken = op(Broken)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("the request must not be sent")

    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    with (
        Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True)) as client,
        pytest.raises(PlanError, match="a str is not a int"),
    ):
        Service(client).broken()
