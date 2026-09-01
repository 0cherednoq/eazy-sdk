from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
from pydantic import BaseModel

from eazy_sdk.response import (
    ApiError,
    Bytes,
    Empty,
    Error,
    Json,
    NormalizedResponse,
    Parsed,
    ResponseContext,
    Responses,
    Success,
    Text,
)
from eazy_sdk.response.cases import (
    AmbiguousResponseOutcome,
    BoundResponseParser,
    ErrorOutcome,
    Malformed,
    MalformedOutcome,
    NoMatch,
    ParseAttempt,
    ParsedValue,
    SuccessOutcome,
    UnexpectedOutcome,
)


class Payment(BaseModel):
    id: str


class Problem(BaseModel):
    code: str


def context(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "application/json",
    headers: tuple[tuple[str, str], ...] = (),
) -> ResponseContext[object]:
    response: NormalizedResponse[object] = NormalizedResponse(
        status,
        "https://api.example/test",
        "GET",
        (("Content-Type", content_type), *headers),
        body,
    )
    return ResponseContext(response)


def test_json_mapping_shorthand_and_canonical_cases_have_same_outcome() -> None:
    shorthand: Responses[Payment] = Responses(success={200: Json(Payment)})
    canonical: Responses[Payment] = Responses(success=(Success(200, Json(Payment)),))
    first = shorthand.inspect(context(b'{"id":"pay_1"}'))
    second = canonical.inspect(context(b'{"id":"pay_1"}'))
    assert isinstance(first, SuccessOutcome) and isinstance(second, SuccessOutcome)
    assert first.value == second.value == Payment(id="pay_1")
    assert shorthand._result_type is Payment


def test_responses_expose_the_runtime_result_type_encoded_by_success_cases() -> None:
    assert Responses[str](success=(Success(200, Text()),))._result_type is str
    assert Responses[bytes](success=(Success(200, Bytes()),))._result_type is bytes
    assert Responses[None](success=(Success(204, Empty()),))._result_type is type(None)
    assert Responses[object](success=())._result_type is None


class PaymentError(ApiError[Problem]):
    pass


def test_documented_error_is_an_outcome_until_terminal_unwrap() -> None:
    responses: Responses[Payment] = Responses(
        success=(Success(201, Json(Payment)),),
        errors=(Error(400, Json(Problem), exception=PaymentError),),
    )
    outcome = responses.inspect(context(b'{"code":"invalid"}', status=400))
    assert isinstance(outcome, ErrorOutcome)
    assert outcome.error == Problem(code="invalid")
    with pytest.raises(PaymentError) as captured:
        outcome.unwrap()
    assert captured.value.error == Problem(code="invalid")


@dataclass(frozen=True)
class Dashboard:
    title: str


@dataclass(frozen=True)
class InvalidCredentials:
    message: str


@dataclass(frozen=True)
class CaptchaRequired:
    captcha_id: str


@dataclass(frozen=True)
class AccountBlocked:
    reason: str


class HtmlParser:
    def __init__(self) -> None:
        self.bind_calls = 0
        self.document_builds = 0
        self.try_calls: list[type[object]] = []

    def bind(self, response: ResponseContext[object]) -> BoundResponseParser:
        self.bind_calls += 1
        document = response.cached(self, lambda: self._document(response))
        return BoundHtmlParser(self, document)

    def _document(self, response: ResponseContext[object]) -> str:
        self.document_builds += 1
        assert response.text.value is not None
        return response.text.value


@dataclass(frozen=True)
class BoundHtmlParser:
    owner: HtmlParser
    document: str

    def try_parse[T](self, model: type[T]) -> ParseAttempt[T]:
        self.owner.try_calls.append(cast(type[object], model))
        if model is Dashboard and "dashboard" in self.document:
            return cast(ParseAttempt[T], ParsedValue(Dashboard("Home")))
        if model is InvalidCredentials and "invalid" in self.document:
            return cast(ParseAttempt[T], ParsedValue(InvalidCredentials("bad credentials")))
        if model is CaptchaRequired and "captcha" in self.document:
            if "data-id=" not in self.document:
                return Malformed(ValueError("captcha id missing"))
            return cast(ParseAttempt[T], ParsedValue(CaptchaRequired("cap_1")))
        if model is AccountBlocked and "blocked" in self.document:
            return cast(ParseAttempt[T], ParsedValue(AccountBlocked("policy")))
        return NoMatch()


def html_responses(parser: HtmlParser) -> Responses[Dashboard]:
    return Responses(
        success=(Success(200, Parsed(Dashboard, using=parser, media_type="text/html")),),
        errors=(
            Error(200, Parsed(InvalidCredentials, using=parser, media_type="text/html")),
            Error(200, Parsed(CaptchaRequired, using=parser, media_type="text/html")),
            Error(200, Parsed(AccountBlocked, using=parser, media_type="text/html")),
        ),
    )


@pytest.mark.parametrize(
    ("html", "outcome_type", "value_type"),
    [
        (b"<main>dashboard</main>", SuccessOutcome, Dashboard),
        (b"<p>invalid</p>", ErrorOutcome, InvalidCredentials),
        (b"<div captcha data-id=cap_1></div>", ErrorOutcome, CaptchaRequired),
        (b"<p>blocked</p>", ErrorOutcome, AccountBlocked),
    ],
)
def test_one_html_endpoint_distinguishes_all_content_variants_with_one_document(
    html: bytes, outcome_type: type[object], value_type: type[object]
) -> None:
    parser = HtmlParser()
    outcome = html_responses(parser).inspect(context(html, content_type="text/html"))
    assert isinstance(outcome, outcome_type)
    if isinstance(outcome, SuccessOutcome):
        assert isinstance(outcome.value, value_type)
    elif isinstance(outcome, ErrorOutcome):
        assert isinstance(outcome.error, value_type)
    else:
        pytest.fail(f"unexpected outcome {outcome!r}")
    assert parser.bind_calls == 1
    assert parser.document_builds == 1


def test_no_match_malformed_and_ambiguity_are_distinct() -> None:
    parser = HtmlParser()
    no_match = html_responses(parser).inspect(context(b"<p>unknown</p>", content_type="text/html"))
    assert isinstance(no_match, UnexpectedOutcome)
    assert no_match.attempted_models == (
        "Dashboard",
        "InvalidCredentials",
        "CaptchaRequired",
        "AccountBlocked",
    )

    malformed = html_responses(HtmlParser()).inspect(
        context(b"<div>captcha</div>", content_type="text/html")
    )
    assert isinstance(malformed, MalformedOutcome)
    assert "captcha id missing" in str(malformed.cause)

    ambiguous = html_responses(HtmlParser()).inspect(
        context(b"dashboard invalid", content_type="text/html")
    )
    assert isinstance(ambiguous, AmbiguousResponseOutcome)
    assert len(ambiguous.cases) == 2


def test_one_responses_object_mixes_json_text_bytes_and_empty() -> None:
    responses: Responses[object] = Responses(
        success=(
            Success(200, Json(Payment)),
            Success(202, Text()),
            Success(203, Bytes()),
            Success(204, Empty()),
        )
    )
    text = responses.inspect(context(b"accepted", status=202, content_type="text/plain"))
    binary = responses.inspect(
        context(b"\x00\xff", status=203, content_type="application/octet-stream")
    )
    empty = responses.inspect(context(b"", status=204, content_type="application/unknown"))
    assert isinstance(text, SuccessOutcome) and text.value == "accepted"
    assert isinstance(binary, SuccessOutcome) and binary.value == b"\x00\xff"
    assert isinstance(empty, SuccessOutcome) and empty.value is None


def test_response_context_caches_views_and_preserves_repeated_cookies() -> None:
    response = context(
        b'{"id":"p"}',
        headers=(("Set-Cookie", "a=1; Path=/"), ("Set-Cookie", "b=2; Path=/")),
    )
    assert response.json is response.json
    assert response.text is response.text
    assert response.headers.getall("set-cookie") == ("a=1; Path=/", "b=2; Path=/")
    assert response.cookies.values == (("a", "1"), ("b", "2"))
