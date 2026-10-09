"""Phase 57.2: a model read from around the body, and ``Location`` as a criterion on its field.

The scenario is the login of phase 57.1 with the outcomes written as models: each one states
where the redirect points, the operation says which of them are a success and which an error,
and the value that comes back carries what the target held.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

import httpx
import msgspec
import pydantic
import pytest

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.models import default_model_adapters
from eazy_sdk.response import (
    AmbiguousResponseError,
    ApiError,
    Const,
    Error,
    FromCookie,
    FromHeader,
    HeaderModel,
    Html,
    Json,
    Location,
    MalformedResponseError,
    NormalizedResponse,
    ResponseContext,
    Responses,
    Success,
    UnexpectedResponseError,
)
from eazy_sdk.response._mapping import representation
from eazy_sdk.response.cases import (
    AmbiguousResponseOutcome,
    ErrorOutcome,
    MalformedOutcome,
    SuccessOutcome,
    UnexpectedOutcome,
    _specificity,
)
from eazy_sdk.response.markers import criteria_of

REQUEST = "https://auth.example/cgi-bin/auth"


def _context(
    location: str | None,
    *,
    code: int = 302,
    body: bytes = b"<html>moved</html>",
    headers: tuple[tuple[str, str], ...] = (),
) -> ResponseContext[object]:
    lines = (("Location", location),) if location is not None else ()
    response: NormalizedResponse[object] = NormalizedResponse(
        code, REQUEST, "POST", (("Content-Type", "text/html"), *lines, *headers), body
    )
    return ResponseContext(response)


# --- the outcomes of a login, as models -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignedIn:
    url: Annotated[str, Location(path="/inbox*")]


@dataclass(frozen=True, slots=True)
class NeedsCode:
    url: Annotated[str, Location(path="/cgi-bin/secstep*")]


@dataclass(frozen=True, slots=True)
class Banned:
    errno: Annotated[str, Location.query("errno"), Const("25")]


@dataclass(frozen=True, slots=True)
class Rejected:
    fail: Annotated[str, Location.query("fail")]


class AccountBanned(ApiError[Banned]):
    """The redirect carried ``errno=25``."""


class InvalidCredentials(ApiError[Rejected]):
    """The redirect carried ``fail=``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCredentials(HttpOperation[SignedIn | NeedsCode]):
    __http__ = Http.post(
        "/cgi-bin/auth",
        success={302: [SignedIn, NeedsCode]},
        errors={302: [AccountBanned, InvalidCredentials]},
    )


class Login(SyncApi):
    submit = op(SubmitCredentials)


def _redirecting(location: str, *, body: bytes = b"<html>moved</html>") -> Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, content=body, headers={"content-type": "text/html", "location": location}
        )

    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(base_url="https://auth.example", handler=HttpxHandler(raw, owns_client=True))


def test_four_outcomes_on_one_status_are_told_apart_by_the_target() -> None:
    with _redirecting("https://mail.example/inbox/") as client:
        assert Login(client).submit() == SignedIn("https://mail.example/inbox/")
    with _redirecting("/cgi-bin/secstep?otp=1") as client:
        assert Login(client).submit() == NeedsCode("https://auth.example/cgi-bin/secstep?otp=1")
    with _redirecting("/login?errno=25") as client, pytest.raises(AccountBanned) as banned:
        Login(client).submit()
    assert banned.value.error == Banned("25")
    with _redirecting("/login?fail=1") as client, pytest.raises(InvalidCredentials) as rejected:
        Login(client).submit()
    assert rejected.value.error == Rejected("1")


def test_the_body_of_a_redirect_is_not_read() -> None:
    with _redirecting("/inbox", body=b"") as client:
        assert Login(client).submit() == SignedIn("https://auth.example/inbox")
    with _redirecting("/inbox", body=b"\xff\xfe not text at all") as client:
        assert Login(client).submit() == SignedIn("https://auth.example/inbox")


def test_a_target_no_model_describes_is_unexpected_and_names_what_was_weighed() -> None:
    with (
        _redirecting("/somewhere/else") as client,
        pytest.raises(UnexpectedResponseError) as raised,
    ):
        Login(client).submit()
    assert "SignedIn" in str(raised.value)


def test_a_constant_beside_the_parameter_narrows_the_case() -> None:
    """``errno`` is there but is not 25: the case declines, and nothing else claims it."""

    with _redirecting("/login?errno=7") as client, pytest.raises(UnexpectedResponseError):
        Login(client).submit()


# --- which family reads a model ---------------------------------------------------------------


class Created(pydantic.BaseModel):
    """A body and a header in one model: still JSON, as with ``FromHeader`` before this phase."""

    id: int
    url: Annotated[str, Location(path="/orders/*")]


def test_a_model_read_entirely_from_around_the_body_is_a_header_model() -> None:
    models = default_model_adapters()
    assert isinstance(representation(SignedIn, models=models), HeaderModel)
    assert isinstance(representation(Banned, models=models), HeaderModel)
    assert isinstance(representation(Created, models=models), Json)


def test_a_body_and_a_location_are_read_into_one_model() -> None:
    responses: Responses[Created] = Responses(success=(Success(201, Json(Created)),))
    response: NormalizedResponse[object] = NormalizedResponse(
        201,
        "https://api.example/orders",
        "POST",
        (("Content-Type", "application/json"), ("Location", "/orders/7")),
        b'{"id": 7}',
    )
    outcome = responses.inspect(ResponseContext(response))
    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value == Created(id=7, url="https://api.example/orders/7")


def test_the_explicit_form_takes_a_condition_of_its_own() -> None:
    shape = HeaderModel(SignedIn, when=Location(host="mail.example"))
    responses: Responses[SignedIn] = Responses(success=(Success(302, shape, shape.when),))
    assert isinstance(responses.inspect(_context("https://mail.example/inbox")), SuccessOutcome)
    assert isinstance(responses.inspect(_context("https://other.example/inbox")), UnexpectedOutcome)


# --- every model backend ----------------------------------------------------------------------


class PydanticSignedIn(pydantic.BaseModel):
    url: Annotated[str, Location(path="/inbox*")]


class PydanticRejected(pydantic.BaseModel):
    fail: Annotated[str, Location.query("fail")]


class StructSignedIn(msgspec.Struct):
    url: Annotated[str, Location(path="/inbox*")]


class StructRejected(msgspec.Struct):
    fail: Annotated[str, Location.query("fail")]


class DictSignedIn(TypedDict):
    url: Annotated[str, Location(path="/inbox*")]


class DictRejected(TypedDict):
    fail: Annotated[str, Location.query("fail")]


@pytest.mark.parametrize(
    ("signed_in", "rejected"),
    [
        (SignedIn, Rejected),
        (PydanticSignedIn, PydanticRejected),
        (StructSignedIn, StructRejected),
        (DictSignedIn, DictRejected),
    ],
    ids=["dataclass", "pydantic", "msgspec", "typeddict"],
)
def test_location_fields_work_on_every_model_backend(signed_in: type, rejected: type) -> None:
    models = default_model_adapters()
    responses: Responses[object] = Responses(
        success=(Success(302, representation(signed_in, models=models)),),
        errors=(Error(302, representation(rejected, models=models)),),
    )
    success = responses.inspect(_context("/inbox/"))
    assert isinstance(success, SuccessOutcome)
    value: Any = success.value
    url = value["url"] if isinstance(value, dict) else value.url
    assert url == "https://auth.example/inbox/"
    failure = responses.inspect(_context("/login?fail=bad"))
    assert isinstance(failure, ErrorOutcome)
    assert isinstance(responses.inspect(_context("/elsewhere")), UnexpectedOutcome)


# --- a miss is another case; an optional field is no criterion --------------------------------


@dataclass(frozen=True, slots=True)
class Landing:
    """States one thing — the path — and picks up whatever else the target carries."""

    url: Annotated[str, Location(path="/landing")]
    authid: Annotated[str | None, Location.query("authid")]
    tags: Annotated[list[str], Location.query("tag")]


def test_an_optional_field_is_filled_when_present_and_left_empty_when_not() -> None:
    responses: Responses[Landing] = Responses(success=(Success(302, HeaderModel(Landing)),))
    full = responses.inspect(_context("/landing?authid=abc&tag=a&tag=b"))
    assert isinstance(full, SuccessOutcome)
    assert full.value == Landing(
        "https://auth.example/landing?authid=abc&tag=a&tag=b", "abc", ["a", "b"]
    )
    bare = responses.inspect(_context("/landing?tag=a"))
    assert isinstance(bare, SuccessOutcome)
    assert bare.value.authid is None


def test_only_a_field_that_cannot_be_none_is_a_criterion() -> None:
    assert [field.name for field in criteria_of(Landing)] == ["url", "tags"]
    assert repr(criteria_of(Rejected)[0]) == "<fail: location query 'fail' is present>"


def test_no_location_at_all_is_another_case_not_a_malformed_response() -> None:
    responses: Responses[SignedIn] = Responses(success=(Success(302, HeaderModel(SignedIn)),))
    assert isinstance(responses.inspect(_context(None)), UnexpectedOutcome)


def test_the_fallback_receives_a_redirect_every_case_declined() -> None:
    responses: Responses[SignedIn] = Responses(
        success=(Success(302, HeaderModel(SignedIn)),),
        fallback=Error(302, HeaderModel(Rejected)),
    )
    outcome = responses.inspect(_context("/login?fail=1"))
    assert isinstance(outcome, ErrorOutcome)
    assert outcome.error == Rejected("1")


def test_two_models_describing_one_target_are_ambiguous() -> None:
    @dataclass(frozen=True, slots=True)
    class AlsoSignedIn:
        url: Annotated[str, Location(host="auth.example")]

    responses: Responses[object] = Responses(
        success=(Success(302, HeaderModel(SignedIn)), Success(302, HeaderModel(AlsoSignedIn)))
    )
    assert isinstance(responses.inspect(_context("/inbox")), AmbiguousResponseOutcome)

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Submit(HttpOperation[SignedIn | AlsoSignedIn]):
        __http__ = Http.post("/cgi-bin/auth", success={302: [SignedIn, AlsoSignedIn]})

    class Api(SyncApi):
        submit = op(Submit)

    with _redirecting("/inbox") as client, pytest.raises(AmbiguousResponseError):
        Api(client).submit()


def test_a_location_on_a_field_ranks_as_a_criterion() -> None:
    stated: Any = Success(302, HeaderModel(SignedIn))
    silent: Any = Success(302, HeaderModel(Landing, when=None))
    plain: Any = Success(302, Json(dict))
    assert _specificity(stated)[0] == 1
    assert _specificity(silent)[0] == 1
    assert _specificity(plain)[0] == 0


# --- FromCookie -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Started:
    url: Annotated[str, Location(path="/inbox*")]
    session: Annotated[str, FromCookie("sid")]
    trace: Annotated[str | None, FromHeader("X-Trace")] = None


def test_a_cookie_the_response_sets_fills_a_field_and_the_last_one_wins() -> None:
    responses: Responses[Started] = Responses(success=(Success(302, HeaderModel(Started)),))
    outcome = responses.inspect(
        _context(
            "/inbox",
            headers=(
                ("Set-Cookie", "sid=first; Path=/"),
                ("Set-Cookie", "other=x"),
                ("Set-Cookie", "sid=second; Path=/; HttpOnly"),
                ("X-Trace", "t-1"),
            ),
        )
    )
    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value == Started("https://auth.example/inbox", "second", "t-1")


def test_a_required_cookie_that_is_missing_is_a_malformed_response() -> None:
    """Unlike a target that does not fit: the case did claim the response, and it is incomplete."""

    responses: Responses[Started] = Responses(success=(Success(302, HeaderModel(Started)),))
    outcome = responses.inspect(_context("/inbox"))
    assert isinstance(outcome, MalformedOutcome)
    assert "cookie 'sid'" in str(outcome.cause)

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Start(HttpOperation[Started]):
        __http__ = Http.post("/cgi-bin/auth", success={302: Started})

    class Api(SyncApi):
        start = op(Start)

    with _redirecting("/inbox") as client, pytest.raises(MalformedResponseError):
        Api(client).start()


def test_a_field_declares_one_source() -> None:
    @dataclass(frozen=True, slots=True)
    class Twice:
        value: Annotated[str, FromCookie("sid"), FromHeader("X-Sid")]

    responses: Responses[Twice] = Responses(success=(Success(302, HeaderModel(Twice)),))
    outcome = responses.inspect(_context("/inbox"))
    assert isinstance(outcome, MalformedOutcome)
    assert "multiple response sources" in str(outcome.cause)


# --- a document is still a document -----------------------------------------------------------


def test_a_model_with_selectors_is_not_mistaken_for_a_header_model() -> None:
    pytest.importorskip("parsel")
    html = pytest.importorskip("eazy_sdk_html")
    page = pydantic.create_model("Page", title=(Annotated[str, html.CSS("title::text")], ...))

    assert isinstance(representation(page, models=default_model_adapters()), Html)


# --- what the declaration alone already proves wrong ------------------------------------------


def _compile(operation: Any) -> None:
    class Api(SyncApi):
        call: Any = op(operation)

    with _redirecting("/inbox") as client:
        Api(client).call()


def test_a_redirect_case_without_a_criterion_is_refused_before_the_request() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Anything(HttpOperation[bytes]):
        __http__ = Http.post("/cgi-bin/auth", success={302: bytes})

    with pytest.raises(PlanError, match="would claim every redirect"):
        _compile(Anything)


def test_a_redirect_range_without_a_criterion_is_refused_too() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Anything(HttpOperation[bytes]):
        __http__ = Http.post("/cgi-bin/auth", success={200: bytes}, errors={"3xx": bytes})

    with pytest.raises(PlanError, match="would claim every redirect"):
        _compile(Anything)


def test_any_target_is_said_out_loud() -> None:
    from eazy_sdk.response import Bytes

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Anything(HttpOperation[bytes]):
        __http__ = Http.post("/cgi-bin/auth", success={302: Bytes(when=Location())})

    _compile(Anything)


def test_a_wide_range_that_merely_includes_redirects_is_left_alone() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Wide(HttpOperation[SignedIn]):
        __http__ = Http.post("/cgi-bin/auth", success={302: SignedIn}, fallback=str)

    _compile(Wide)


def test_one_model_states_one_location_pattern() -> None:
    @dataclass(frozen=True, slots=True)
    class Two:
        first: Annotated[str, Location(path="/a")]
        second: Annotated[str, Location(host="b.example")]

    with pytest.raises(PlanError, match="Location pattern on 2 fields: first, second"):
        criteria_of(Two)


@pytest.mark.parametrize(
    ("annotation", "marker", "message"),
    [
        (int, Location(path="/a"), r"Location\(\.\.\.\) on Wrong.value: the field takes str"),
        (list[str], Location(path="/a"), r"Location\(\.\.\.\) on Wrong.value"),
        (int, Location.query("n"), r"Location\.query\(\.\.\.\) on Wrong.value"),
        (list[int], Location.query("n"), r"takes str or list\[str\]"),
    ],
)
def test_a_field_a_target_cannot_fill_is_refused(
    annotation: object, marker: object, message: str
) -> None:
    wrong: Any = dataclass(
        type("Wrong", (), {"__annotations__": {"value": Annotated[annotation, marker]}})
    )
    with pytest.raises(PlanError, match=message):
        criteria_of(wrong)


def test_a_parameter_without_a_name_is_refused() -> None:
    with pytest.raises(PlanError, match=r"Location\.query"):
        Location.query("")
