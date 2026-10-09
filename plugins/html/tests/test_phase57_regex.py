"""Phase 57.4: ``Regex`` as a third selector language, and response sources inside a document.

The scenario is a webmail page that keeps the session token in an inline script: no markup
addresses it, the page title says whether this is the mailbox at all, and a cookie set by the
same response belongs to the same session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated

import pytest
from eazy_sdk_html import (
    CSS,
    ExtractionCompileError,
    ExtractionError,
    ParselBackend,
    Regex,
    Scope,
    compile_extraction_schema,
    parse_html,
)
from pydantic import BaseModel

from eazy_sdk.models import default_model_adapters
from eazy_sdk.response import (
    Const,
    FromCookie,
    FromHeader,
    Html,
    NormalizedResponse,
    ResponseContext,
    Responses,
    Success,
)
from eazy_sdk.response._mapping import representation
from eazy_sdk.response.cases import MalformedOutcome, SuccessOutcome, UnexpectedOutcome

MAIL_PAGE = """
<html><head><title>Mail</title></head><body>
<script>
  window.state = {"authCSRFToken":"csrf-1","other":{"token":"decoy","email":"decoy@x"},
    "/api/v1/user/short":{"body":{"token":"tok-1","email":"user@mail.example",
    "name":"A &amp; B","next":"\\u002Finbox"}}};
</script>
<ul><li class="folder">Inbox <i>id=0</i></li><li class="folder">Sent <i>id=500</i></li></ul>
</body></html>
"""

LOGIN_PAGE = "<html><head><title>Sign in</title></head><body><form></form></body></html>"


class MailPage(BaseModel):
    title: Annotated[str, CSS("title::text"), Const("Mail")]
    token: Annotated[str, Regex(r'/api/v1/user/short.*?"token":"([^"]+)"', re.DOTALL)]
    email: Annotated[str, Regex(r'/api/v1/user/short.*?"email":"([^"]+)"', re.DOTALL)]
    csrf: Annotated[str | None, Regex(r'"authCSRFToken":"([^"]+)"')] = None


class Folder(BaseModel):
    name: Annotated[str, CSS("li::text")]
    folder_id: Annotated[str, Regex(r"id=(\d+)")]


class Folders(BaseModel):
    folders: Annotated[list[Folder], Scope(CSS("li.folder"))]


class Item(BaseModel):
    name: Annotated[str, CSS("b::text")]


class ScopedByRegex(BaseModel):
    items: Annotated[list[Item], Scope(Regex(r"<li>.*?</li>"))]


# --- reading text no markup addresses ---------------------------------------------------------


def test_a_regex_reads_a_value_out_of_an_inline_script() -> None:
    page = parse_html(MAIL_PAGE, MailPage)
    assert page.model_dump() == {
        "title": "Mail",
        "token": "tok-1",
        "email": "user@mail.example",
        "csrf": "csrf-1",
    }


def test_the_pattern_anchors_past_a_decoy_instead_of_taking_the_first_key() -> None:
    class Naive(BaseModel):
        token: Annotated[str, Regex(r'"token":"([^"]+)"')]

    assert parse_html(MAIL_PAGE, Naive).token == "decoy"
    assert parse_html(MAIL_PAGE, MailPage).token == "tok-1"


def test_a_single_value_takes_the_first_match_and_a_list_every_match() -> None:
    class Ids(BaseModel):
        first: Annotated[str, Regex(r"id=(\d+)")]
        every: Annotated[list[str], Regex(r"id=(\d+)")]

    ids = parse_html(MAIL_PAGE, Ids)
    assert (ids.first, ids.every) == ("0", ["0", "500"])


def test_a_pattern_without_a_group_yields_the_whole_match() -> None:
    class Whole(BaseModel):
        folder: Annotated[str, Regex(r"id=\d+")]

    assert parse_html(MAIL_PAGE, Whole).folder == "id=0"


def test_the_text_is_searched_as_it_was_received() -> None:
    """Entities are not decoded and escapes are not interpreted: the pattern sees the source."""

    class Raw(BaseModel):
        name: Annotated[str, Regex(r'"name":"([^"]+)"')]
        next_url: Annotated[str, Regex(r'"next":"([^"]+)"')]

    raw = parse_html(MAIL_PAGE, Raw)
    assert raw.name == "A &amp; B"
    assert raw.next_url == "\\u002Finbox"


def test_flags_are_part_of_the_selector() -> None:
    class Loud(BaseModel):
        title: Annotated[str, Regex(r"<TITLE>(.*?)</TITLE>", re.IGNORECASE)]

    assert parse_html(MAIL_PAGE, Loud).title == "Mail"


def test_an_optional_field_stays_empty_and_a_required_one_fails_the_page() -> None:
    class Optional(BaseModel):
        missing: Annotated[str | None, Regex(r"nothing-(\d+)")] = None

    class Required(BaseModel):
        missing: Annotated[str, Regex(r"nothing-(\d+)")]

    assert parse_html(MAIL_PAGE, Optional).missing is None
    with pytest.raises(ExtractionError, match="expected one value, got 0"):
        parse_html(MAIL_PAGE, Required)


def test_a_regex_under_a_scope_reads_the_markup_of_each_node() -> None:
    folders = parse_html(MAIL_PAGE, Folders).folders
    assert [(item.name.strip(), item.folder_id) for item in folders] == [
        ("Inbox", "0"),
        ("Sent", "500"),
    ]


def test_regex_works_on_a_dataclass_model() -> None:
    @dataclass(frozen=True, slots=True)
    class Token:
        token: Annotated[str, Regex(r'/api/v1/user/short.*?"token":"([^"]+)"', re.DOTALL)]

    assert parse_html(MAIL_PAGE, Token) == Token("tok-1")


# --- as a response case -----------------------------------------------------------------------


def _context(body: str, headers: tuple[tuple[str, str], ...] = ()) -> ResponseContext[object]:
    response: NormalizedResponse[object] = NormalizedResponse(
        200,
        "https://mail.example/inbox/",
        "GET",
        (("Content-Type", "text/html; charset=utf-8"), *headers),
        body.encode(),
    )
    return ResponseContext(response)


def test_a_model_with_a_regex_field_is_a_document_model() -> None:
    assert isinstance(representation(MailPage, models=default_model_adapters()), Html)


def test_the_page_claims_the_mailbox_and_declines_the_login_form() -> None:
    responses: Responses[MailPage] = Responses(success=(Success(200, Html(MailPage)),))
    outcome = responses.inspect(_context(MAIL_PAGE))
    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value.token == "tok-1"
    assert not isinstance(responses.inspect(_context(LOGIN_PAGE)), SuccessOutcome)


class SessionPage(BaseModel):
    """The token from the page, the cookie and the trace header from the response around it."""

    token: Annotated[str, Regex(r'/api/v1/user/short.*?"token":"([^"]+)"', re.DOTALL)]
    sid: Annotated[str, FromCookie("sid")]
    trace: Annotated[str | None, FromHeader("X-Trace")] = None


def test_a_document_model_takes_fields_from_around_the_body_too() -> None:
    assert isinstance(representation(SessionPage, models=default_model_adapters()), Html)
    responses: Responses[SessionPage] = Responses(success=(Success(200, Html(SessionPage)),))
    outcome = responses.inspect(
        _context(MAIL_PAGE, (("Set-Cookie", "sid=s-1; Path=/"), ("X-Trace", "t-1")))
    )
    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value.model_dump() == {"token": "tok-1", "sid": "s-1", "trace": "t-1"}


def test_a_missing_cookie_on_the_right_page_is_malformed_not_another_page() -> None:
    responses: Responses[SessionPage] = Responses(success=(Success(200, Html(SessionPage)),))
    assert isinstance(responses.inspect(_context(MAIL_PAGE)), MalformedOutcome)
    assert isinstance(
        responses.inspect(_context(LOGIN_PAGE, (("Set-Cookie", "sid=s-1"),))),
        MalformedOutcome | UnexpectedOutcome,
    )


def test_only_what_the_body_holds_tells_one_page_from_another() -> None:
    """A model whose only required fields come from around the body matches any document."""

    class OnlyCookie(BaseModel):
        sid: Annotated[str, FromCookie("sid")]
        note: Annotated[str | None, CSS("p.note::text")] = None

    schema = compile_extraction_schema(OnlyCookie)
    assert [field.model_field.name for field in schema.fields] == ["note"]
    assert not schema.has_required_field
    assert compile_extraction_schema(SessionPage).has_required_field


# --- a wrong declaration is refused where it is written ----------------------------------------


def test_a_pattern_keeps_one_group_or_none() -> None:
    with pytest.raises(ExtractionCompileError, match="has 2 groups"):
        Regex(r"(a)(b)")
    assert Regex(r"(?:a|b)(c)").search("ac bc") == ("c", "c")


def test_a_pattern_that_does_not_compile_is_refused() -> None:
    with pytest.raises(ExtractionCompileError, match="does not compile"):
        Regex(r"(unclosed")
    with pytest.raises(ValueError, match="must not be empty"):
        Regex("")


def test_a_scope_cannot_be_a_regex() -> None:
    with pytest.raises(ExtractionCompileError, match="a Scope selects nodes"):
        compile_extraction_schema(ScopedByRegex)


def test_a_field_has_one_source() -> None:
    class Both(BaseModel):
        sid: Annotated[str, FromCookie("sid"), CSS("b::text")]

    class TwoSelectors(BaseModel):
        value: Annotated[str, Regex(r"a"), CSS("b::text")]

    with pytest.raises(ExtractionCompileError, match="a field has one source"):
        compile_extraction_schema(Both)
    with pytest.raises(ExtractionCompileError, match="conflicting HTML selector metadata"):
        compile_extraction_schema(TwoSelectors)


def test_a_backend_that_does_not_speak_regex_refuses_the_model() -> None:
    @dataclass(frozen=True, slots=True)
    class MarkupOnly:
        name: str = "markup-only"

        @property
        def selector_languages(self) -> frozenset[str]:
            return frozenset({"css", "xpath"})

        @property
        def media_types(self) -> frozenset[str]:
            return frozenset({"text/html"})

        def parse(self, data: bytes | str) -> object:
            return ParselBackend().parse(data)

    with pytest.raises(ExtractionCompileError, match="selects by 'regex'"):
        compile_extraction_schema(MailPage, backend=MarkupOnly())  # type: ignore[arg-type]
    assert "regex" in ParselBackend().selector_languages
