"""Phase 57.1: ``Location`` — the redirect target compared part by part, as a ``when=`` condition.

The scenario is a login that reports its outcome in a redirect: 302 to the mailbox, to a second
factor page, or back with ``fail=`` or ``errno=25`` in the query. Nothing in the body tells the
outcomes apart, and the transport does not follow the redirect, so the operation reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
import pytest

from eazy_sdk import Client, ClientConfig, Http, HttpOperation, Resilience, SyncApi, op
from eazy_sdk.clients.base import RedirectLimitError
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import (
    ApiError,
    Bytes,
    Json,
    Location,
    NormalizedResponse,
    ResponseContext,
    Text,
    UnexpectedResponseError,
    match,
)
from eazy_sdk.response.location import resolved_location

REQUEST = "https://auth.example/cgi-bin/auth?from=web"


def _context(*locations: str, url: str = REQUEST, code: int = 302) -> ResponseContext[object]:
    response: NormalizedResponse[object] = NormalizedResponse(
        code,
        url,
        "POST",
        tuple(("Location", value) for value in locations),
        b"<html>moved</html>",
    )
    return ResponseContext(response)


# --- every part is optional -------------------------------------------------------------------


def test_a_bare_location_states_only_that_the_header_is_there() -> None:
    assert Location()(_context("https://mail.example/inbox"))
    assert not Location()(_context())


def test_each_part_is_compared_on_its_own() -> None:
    target = _context("https://mail.example/inbox/?folder=0")
    assert Location(host="mail.example")(target)
    assert Location(path="/inbox/")(target)
    assert Location(query={"folder": "0"})(target)
    assert Location(contains="example/inbox")(target)
    assert not Location(host="auth.example")(target)
    assert not Location(path="/login")(target)
    assert not Location(query={"folder": "1"})(target)
    assert not Location(contains="secstep")(target)


def test_the_parts_given_are_all_required_at_once() -> None:
    target = _context("https://mail.example/inbox")
    assert Location(host="mail.example", path="/inbox")(target)
    assert not Location(host="mail.example", path="/login")(target)
    assert not Location(host="auth.example", path="/inbox")(target)


def test_a_pattern_without_a_host_reads_any_host() -> None:
    pattern = Location(path="/cgi-bin/secstep*")
    assert pattern(_context("https://auth.example/cgi-bin/secstep?otp=1"))
    assert pattern(_context("https://auth.other.example/cgi-bin/secstep"))


# --- the glob ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/inbox*", "/inbox", True),
        ("/inbox*", "/inbox/1/2", True),
        ("/inbox*", "/x/inbox", False),
        ("*/secstep", "/cgi-bin/secstep", True),
        ("/cgi-bin/*/done", "/cgi-bin/a/b/done", True),
        ("/cgi-bin/*/done", "/cgi-bin/done", False),
        ("/inbox", "/inbox/", False),
        ("/Inbox", "/inbox", False),
    ],
)
def test_a_star_is_any_run_of_characters_and_the_path_keeps_its_case(
    pattern: str, path: str, expected: bool
) -> None:
    assert Location(path=pattern)(_context(f"https://mail.example{path}")) is expected


def test_question_mark_and_bracket_are_literal_in_a_glob() -> None:
    """An address carries them literally, so the glob does not treat them as wildcards."""

    assert Location(path="/a[1]")(_context("https://mail.example/a[1]"))
    assert not Location(path="/a[1]")(_context("https://mail.example/a1"))
    assert not Location(path="/a?")(_context("https://mail.example/ab"))


def test_a_compiled_pattern_is_matched_against_the_whole_part() -> None:
    target = _context("https://mail.example/inbox/42")
    assert Location(path=re.compile(r"/inbox/\d+"))(target)
    assert not Location(path=re.compile(r"/inbox"))(target)
    assert Location(host=re.compile(r"(mail|web)\.example"))(target)


def test_the_host_ignores_case_and_the_port() -> None:
    target = _context("https://Mail.Example:8443/inbox")
    assert Location(host="mail.example")(target)
    assert Location(host="*.EXAMPLE")(target)
    assert not Location(host="mail.example:8443")(target)


# --- relative targets -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "resolved"),
    [
        ("/inbox", "https://auth.example/inbox"),
        ("secstep?otp=1", "https://auth.example/cgi-bin/secstep?otp=1"),
        ("../x", "https://auth.example/x"),
        ("?fail=1", "https://auth.example/cgi-bin/auth?fail=1"),
        ("//mail.example/inbox", "https://mail.example/inbox"),
    ],
)
def test_a_relative_header_is_resolved_against_the_request_address(
    header: str, resolved: str
) -> None:
    target = resolved_location(_context(header))
    assert target is not None
    assert target.url == resolved


def test_a_relative_target_matches_the_same_declaration_as_an_absolute_one() -> None:
    pattern = Location(host="auth.example", path="/inbox")
    assert pattern(_context("/inbox"))
    assert pattern(_context("https://auth.example/inbox"))


def test_an_address_without_a_path_reads_as_the_root() -> None:
    assert Location(path="/")(_context("https://mail.example"))


# --- the query --------------------------------------------------------------------------------


def test_query_values_are_decoded_and_a_repeated_parameter_keeps_every_value() -> None:
    target = _context("https://auth.example/login?fail=bad%20password&tag=a&tag=b&empty=")
    assert Location(query={"fail": "bad password"})(target)
    assert Location(query={"tag": "b"})(target)
    assert Location(query={"tag": re.compile(r"[ab]")})(target)
    assert not Location(query={"tag": re.compile(r"c")})(target)
    assert Location(query={"empty": ...})(target)
    assert Location(query={"empty": ""})(target)
    assert not Location(query={"missing": ...})(target)


def test_several_query_parameters_are_all_required() -> None:
    target = _context("https://auth.example/login?errno=25&from=web")
    assert Location(query={"errno": "25", "from": ...})(target)
    assert not Location(query={"errno": "25", "fail": ...})(target)


# --- no target is another case, never an error ------------------------------------------------


def test_no_header_or_several_headers_match_no_pattern() -> None:
    assert not Location(path="/inbox")(_context())
    assert not Location()(_context("https://a.example/inbox", "https://b.example/inbox"))
    assert resolved_location(_context()) is None


def test_an_address_that_does_not_parse_matches_no_pattern() -> None:
    assert not Location()(_context("http://[broken/inbox"))


def test_the_header_is_resolved_once_for_every_pattern_that_reads_it() -> None:
    context = _context("https://mail.example/inbox")
    assert resolved_location(context) is resolved_location(context)


# --- composition and diagnostics --------------------------------------------------------------


def test_a_location_combines_with_predicates_and_plain_callables() -> None:
    mailbox = _context("https://mail.example/inbox")
    login = _context("https://auth.example/login?fail=1")
    either = Location(path="/inbox*") | Location(query={"fail": ...})
    assert either(mailbox) and either(login)
    assert (Location(path="/inbox*") & match.status.is_(302))(mailbox)
    assert not (Location(path="/inbox*") & match.status.is_(301))(mailbox)
    assert (match.status.is_(302) & Location(path="/inbox*"))(mailbox)
    assert (~Location(path="/inbox*"))(login)
    assert (Location() & (lambda context: context.response.status_code == 302))(mailbox)


def test_the_label_names_the_parts_that_were_weighed() -> None:
    assert Location().label == "location is present"
    assert Location(path="/inbox*").label == "location path '/inbox*'"
    pattern = Location(
        host="mail.example",
        path=re.compile(r"/inbox/\d+"),
        query={"errno": "25", "fail": ...},
        contains="x",
    )
    assert pattern.label == (
        "location host 'mail.example' and path matches '/inbox/\\\\d+' and "
        "query 'errno' '25' and query 'fail' is present and contains 'x'"
    )
    assert repr(Location(path="/inbox*")) == "<location path '/inbox*'>"


def test_the_label_survives_composition_from_either_side() -> None:
    assert (match.status.is_(302) & Location(path="/a")).label == (
        "(status is 302 and location path '/a')"
    )
    assert (Location(path="/a") | match.status.is_(301)).label == (
        "(location path '/a' or status is 301)"
    )
    assert (~Location()).label == "not location is present"


def test_two_equal_patterns_are_equal_and_hash_alike() -> None:
    left = Location(path="/inbox*", query={"a": "1", "b": ...})
    right = Location(path="/inbox*", query={"b": ..., "a": "1"})
    assert left == right and hash(left) == hash(right)
    assert left != Location(path="/inbox*")
    assert left != "location"


# --- a wrong declaration is refused where it is written ----------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        {"host": ""},
        {"path": ""},
        {"path": 5},
        {"path": re.compile(rb"/inbox")},
        {"contains": ""},
        {"query": ["fail"]},
        {"query": {"": "1"}},
        {"query": {"fail": 1}},
        {"query": {"fail": None}},
    ],
)
def test_an_argument_a_pattern_cannot_hold_is_refused(arguments: dict[str, object]) -> None:
    with pytest.raises(PlanError, match="Location"):
        Location(**arguments)  # type: ignore[arg-type]


# --- written straight into ``when=`` ----------------------------------------------------------


class InvalidCredentials(ApiError[str]):
    """The login page came back with ``fail=`` in the query."""


class AccountBanned(ApiError[str]):
    """The login page came back with ``errno=25``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCredentials(HttpOperation[bytes]):
    __http__ = Http.post(
        "/cgi-bin/auth",
        success={302: Bytes(when=Location(path="/inbox*"))},
        errors={
            302: [
                (Text(media_type=None, when=Location(query={"errno": "25"})), AccountBanned),
                (Text(media_type=None, when=Location(query={"fail": ...})), InvalidCredentials),
            ]
        },
    )


class Login(SyncApi):
    submit = op(SubmitCredentials)


def _redirecting(location: str, *, max_redirects: int = 0, seen: list[str] | None = None) -> Client:
    """A service that answers its entry point with a redirect and everything else with 200."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(f"{request.method} {request.url.path}")
        if request.url.path != "/cgi-bin/auth":
            return httpx.Response(200, json={"followed": True})
        return httpx.Response(
            302,
            content=b"<html>moved</html>",
            headers={"content-type": "text/html", "location": location},
        )

    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(
        base_url="https://auth.example",
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(resilience=Resilience(max_redirects=max_redirects)),
    )


def test_a_redirect_reaches_the_operation_and_its_target_selects_the_case() -> None:
    with _redirecting("https://mail.example/inbox/") as client:
        assert Login(client).submit() == b"<html>moved</html>"
    with _redirecting("/login?fail=1") as client, pytest.raises(InvalidCredentials):
        Login(client).submit()
    with _redirecting("/login?errno=25") as client, pytest.raises(AccountBanned):
        Login(client).submit()


def test_a_redirect_no_case_describes_is_unexpected() -> None:
    with _redirecting("/somewhere/else") as client, pytest.raises(UnexpectedResponseError):
        Login(client).submit()


# --- who reads a redirect: the operation that declares it, otherwise the client ----------------


def test_a_declared_redirect_is_read_and_not_followed_even_with_a_redirect_budget() -> None:
    seen: list[str] = []
    with _redirecting("https://auth.example/inbox", max_redirects=3, seen=seen) as client:
        assert Login(client).submit() == b"<html>moved</html>"
    assert seen == ["POST /cgi-bin/auth"]


def test_a_declared_redirect_no_case_describes_is_not_followed_either() -> None:
    """The operation owns the status: a target it did not describe is unexpected, not a hop."""

    seen: list[str] = []
    with (
        _redirecting("/somewhere/else", max_redirects=3, seen=seen) as client,
        pytest.raises(UnexpectedResponseError),
    ):
        Login(client).submit()
    assert seen == ["POST /cgi-bin/auth"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitPlain(HttpOperation[dict[str, bool]]):
    """Declares nothing on a redirect status; its fallback describes what nothing else claimed."""

    __http__ = Http.post("/cgi-bin/auth", fallback=Json(dict[str, bool]))


class PlainLogin(SyncApi):
    submit = op(SubmitPlain)


def test_an_undeclared_redirect_is_still_followed_by_the_client() -> None:
    seen: list[str] = []
    with _redirecting("/landing", max_redirects=1, seen=seen) as client:
        assert PlainLogin(client).submit() == {"followed": True}
    assert seen == ["POST /cgi-bin/auth", "GET /landing"]


def test_a_fallback_does_not_claim_a_redirect_the_client_has_no_budget_for() -> None:
    with _redirecting("/landing") as client, pytest.raises(RedirectLimitError):
        PlainLogin(client).submit()


def test_responses_declare_a_status_by_exact_code_or_range_but_not_by_default() -> None:
    from eazy_sdk.response import DEFAULT, Error, Responses, StatusRange, Success

    exact: Responses[bytes] = Responses(success=(Success(302, Bytes()),))
    ranged: Responses[bytes] = Responses(
        success=(Success(200, Bytes()),), errors=(Error(StatusRange(300, 399), Bytes()),)
    )
    default: Responses[bytes] = Responses(
        success=(Success(200, Bytes()),), fallback=Error(DEFAULT, Bytes())
    )
    assert exact.declares(302) and not exact.declares(301)
    assert ranged.declares(307)
    assert not default.declares(302)
