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
    Payload,
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
from eazy_sdk.response.markers import PayloadField, Tag, payload_of, tags_of

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


# --- 54.3: Payload[T] projects the envelope away -----------------------------------------------


class Page(msgspec.Struct):
    number: int


class PageEnvelope(msgspec.Struct):
    success: Annotated[bool, Const(True)]
    result: Payload[Page]
    message: str | None = None


class PageFailure(msgspec.Struct):
    success: Annotated[bool, Const(False)]
    message: str


class PageFailed(ApiError[PageFailure]):
    """The failure keeps the whole envelope: its message lives there and nowhere else."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FetchPage(HttpOperation[Page]):
    __http__ = Http.get(
        "/page",
        success={200: Json(PageEnvelope)},
        errors={200: (Json(PageFailure), PageFailed)},
    )


class Pages(SyncApi):
    fetch = op(FetchPage)


def test_payload_becomes_the_operation_result() -> None:
    """The envelope is a detail of the declaration; the call site sees the payload."""

    body = {"success": True, "result": {"number": 7}, "message": None}
    with _serve(body) as client:
        assert Pages(client).fetch() == Page(number=7)


def test_payload_is_not_applied_to_an_error() -> None:
    """An ApiError reports the message, which lives in the envelope and not in the payload."""

    with (
        _serve({"success": False, "message": "case not found"}) as client,
        pytest.raises(PageFailed) as raised,
    ):
        Pages(client).fetch()
    assert raised.value.error == PageFailure(success=False, message="case not found")


def test_payload_states_the_result_type() -> None:
    """Read from the annotation, where the author already writes it."""

    # ``Responses[Page]`` cannot be written here: once a model projects to the payload, the
    # case's model is no longer the operation's result type, and no type checker ties the two.
    # That is what D-54-03 is for, and why it is checked at runtime.
    responses: Responses[object] = Responses(success=(Success(200, Json(PageEnvelope)),))
    assert responses._result_type is Page
    assert payload_of(PageEnvelope) == PayloadField("result", Page)


def test_payload_type_is_checked_against_the_operation() -> None:
    """D-54-01's sibling: a projection that disagrees with the operation is a typo, not a call."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Disagrees(HttpOperation[str]):
        __http__ = Http.get("/page", success={200: Json(PageEnvelope)})

    class Service(SyncApi):
        fetch = op(Disagrees)

    with (
        _never_served() as client,
        pytest.raises(PlanError, match="Payload is Page, the operation returns str"),
    ):
        Service(client).fetch()


def test_two_payloads_on_one_model_are_refused() -> None:
    """D-54-02."""

    class TwoPayloads(msgspec.Struct):
        result: Payload[Page]
        data: Payload[Page]

    with pytest.raises(PlanError, match=r"TwoPayloads declares Payload on 2 fields: result, data"):
        payload_of(TwoPayloads)


def test_unwrap_and_payload_together_are_refused() -> None:
    """D-54-05: the pointer reads the payload before parsing, the marker after."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Both(HttpOperation[Page]):
        __http__ = Http.get("/page", success={200: Json(PageEnvelope, unwrap="/result")})

    class Service(SyncApi):
        fetch = op(Both)

    with (
        _never_served() as client,
        pytest.raises(PlanError, match="reads the payload before parsing"),
    ):
        Service(client).fetch()


def _never_served() -> Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("the request must not be sent")

    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True))


# --- 54.4: Envelope is gone, and the declaration checks read tags ------------------------------


def test_envelope_is_gone_from_the_public_surface() -> None:
    import eazy_sdk.response as response_package

    assert "Envelope" not in response_package.__all__
    assert not hasattr(response_package, "Envelope")


def test_a_model_that_still_declares_an_envelope_is_refused() -> None:
    """D-54-06: nothing reads ``__envelope__`` now, so a model keeping one routes by nothing."""

    @dataclass(frozen=True)
    class Leftover:
        result: Page | None
        success: bool

        __envelope__ = object()

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Leftover]):
        __http__ = Http.get("/page", success={200: Json(Leftover)})

    class Service(SyncApi):
        fetch = op(Fetch)

    with (
        _never_served() as client,
        pytest.raises(PlanError, match="Envelope was replaced by Const"),
    ):
        Service(client).fetch()


def test_one_model_on_both_sides_with_no_tag_is_refused() -> None:
    """D-54-04, now naming the tag as the fix."""

    @dataclass(frozen=True)
    class Plain:
        result: Page | None
        success: bool

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Plain]):
        __http__ = Http.get(
            "/page",
            success={200: Json(Plain)},
            errors={200: (Json(Plain), PageFailed)},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with (
        _never_served() as client,
        pytest.raises(PlanError, match=r"add Const\(\.\.\.\) to a field of Plain"),
    ):
        Service(client).fetch()


def test_a_tag_tells_the_pair_apart() -> None:
    """Declaring what the diagnostic asks for is what clears it."""

    body = {"success": True, "result": {"number": 7}, "message": None}
    with _serve(body) as client:
        assert Pages(client).fetch() == Page(number=7)


def test_payload_applies_whichever_criterion_decided() -> None:
    """``accept=`` replaces the criterion only; where the payload sits is still the model's."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Page]):
        __http__ = Http.get("/page", success={200: Json(PageEnvelope, accept=lambda _r: True)})

    class Service(SyncApi):
        fetch = op(Fetch)

    with _serve({"success": False, "result": {"number": 7}, "message": None}) as client:
        assert Service(client).fetch() == Page(number=7)
