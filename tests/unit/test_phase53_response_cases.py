"""Phase 53: one style for response cases, and a condition that outranks status precision.

The scenario throughout is the one that motivated the phase: a service answers 200 with either
the real payload or a protection page, and the protection cases are declared once for the whole
service. Nothing about that should force every operation to repeat the negation of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import (
    AmbiguousResponseError,
    ApiError,
    Bytes,
    Empty,
    Error,
    Extracted,
    Html,
    Json,
    MalformedResponseError,
    NormalizedResponse,
    Parsed,
    ResponseContext,
    ResponseExtractor,
    StatusRange,
    Success,
    Text,
    match,
)
from eazy_sdk.response.cases import ResponseParser, _specificity

BASE = "https://api.example"

PDF = b"%PDF-1.7 the real document"
CHALLENGE = b"<html>pravocaptcha.execute()</html>"
BLOCKED = b"<html>support@example.test</html>"


def is_pdf(context: ResponseContext[object]) -> bool:
    return context.bytes.startswith(b"%PDF-")


def is_challenge(context: ResponseContext[object]) -> bool:
    return b"pravocaptcha" in context.bytes


def is_blocked(context: ResponseContext[object]) -> bool:
    return b"support@example.test" in context.bytes


class ChallengeRequired(ApiError[str]):
    """The protection page, declared once for a whole service."""


class AccessBlocked(ApiError[str]):
    pass


def _serve(body: bytes, status: int = 200, media: str = "text/html") -> Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers={"content-type": media})

    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True))


# --- 53.1: every representation takes ``when=`` -----------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadPdf(HttpOperation[bytes]):
    """``Bytes`` could not carry a condition before this phase; only ``Success(...)`` could."""

    __http__ = Http.get(
        "/document",
        success={200: Bytes(media_type=None, when=is_pdf)},
        errors={200: (Text(media_type=None, when=is_challenge), ChallengeRequired)},
    )


class Documents(SyncApi):
    download = op(DownloadPdf)


def test_when_on_bytes_selects_pdf_over_html() -> None:
    with _serve(PDF, media="application/pdf") as client:
        assert Documents(client).download() == PDF


def test_when_on_bytes_lets_the_error_case_take_the_challenge() -> None:
    with _serve(CHALLENGE) as client, pytest.raises(ChallengeRequired):
        Documents(client).download()


def test_when_on_text_empty_extracted_and_parsed() -> None:
    """The other four representations accept the same keyword, with the same meaning."""

    for representation in (
        Text(media_type=None, when=is_pdf),
        Bytes(media_type=None, when=is_pdf),
        Empty(media_type=None, when=is_pdf),
    ):
        assert representation.when is is_pdf

    @dataclass(frozen=True)
    class Model:
        value: str

    extracted = Extracted(Model, using=cast(ResponseExtractor, _never_extractor()), when=is_pdf)
    parsed = Parsed(Model, using=cast(ResponseParser, _never_parser()), when=is_pdf)
    assert extracted.when is is_pdf
    assert parsed.when is is_pdf


def _never_extractor() -> object:
    class Extractor:
        name = "never"

        def bind(self, response: ResponseContext[object]) -> object:
            raise NotImplementedError

    return Extractor()


def _never_parser() -> object:
    class Parser:
        name = "never"

        def bind(self, response: ResponseContext[object]) -> object:
            raise NotImplementedError

    return Parser()


def test_text_when_tells_two_protection_pages_apart() -> None:
    """Two error cases on one status, told apart only by ``when=`` on ``Text``."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[bytes]):
        __http__ = Http.get(
            "/document",
            success={200: Bytes(media_type=None, when=is_pdf)},
            errors={
                200: [
                    (Text(media_type=None, when=is_challenge), ChallengeRequired),
                    (Text(media_type=None, when=is_blocked), AccessBlocked),
                ]
            },
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with _serve(CHALLENGE) as client, pytest.raises(ChallengeRequired):
        Service(client).fetch()
    with _serve(BLOCKED) as client, pytest.raises(AccessBlocked):
        Service(client).fetch()
    with _serve(PDF, media="application/pdf") as client:
        assert Service(client).fetch() == PDF


def test_representation_and_factory_is_a_valid_error_entry() -> None:
    """The dict form pairs a written-out representation with its exception, not only a model."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[bytes]):
        __http__ = Http.get(
            "/document",
            success={200: Bytes(media_type=None, when=is_pdf)},
            errors={
                StatusRange(200, 599): (Text(media_type=None, when=is_challenge), ChallengeRequired)
            },
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with _serve(CHALLENGE, status=503) as client, pytest.raises(ChallengeRequired) as raised:
        Service(client).fetch()
    assert raised.value.summary.status_code == 503


def test_error_entry_still_rejects_a_malformed_pair() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[bytes]):
        __http__ = Http.get(
            "/document",
            errors={404: cast(Any, ("not a model", ChallengeRequired))},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with pytest.raises(
        PlanError, match=r"must be \(Model, factory\) or \(Representation, factory\)"
    ):
        Service(_serve(PDF)).fetch()


def test_service_errors_still_take_the_object_form() -> None:
    """``Error(...)`` keeps working: it is the advanced form, not a removed one."""

    class Protected(SyncApi):
        errors: tuple[Error[str], ...] = (
            Error(
                StatusRange(200, 599),
                Text(media_type=None),
                exception=ChallengeRequired,
                condition=is_challenge,
            ),
        )

        fetch = op(DownloadPdf)

    with _serve(CHALLENGE) as client, pytest.raises(ChallengeRequired):
        Protected(client).fetch()


def test_json_and_html_keep_their_when() -> None:
    """53.1 adds the keyword where it was missing without moving it where it already was."""

    @dataclass(frozen=True)
    class Payload:
        kind: str

    assert Json(Payload, when=is_pdf).when is is_pdf
    assert Html(Payload, when=is_pdf).when is is_pdf


# --- 53.2: a criterion outranks status precision ----------------------------------------------


class Protection(SyncApi):
    """A service declares its protection pages once, on a range, with conditions."""

    errors: tuple[Error[str], ...] = (
        Error(
            StatusRange(200, 599),
            Text(media_type=None),
            exception=ChallengeRequired,
            condition=is_challenge,
        ),
        Error(
            StatusRange(200, 599),
            Text(media_type=None),
            exception=AccessBlocked,
            condition=is_blocked,
        ),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PlainDownload(HttpOperation[bytes]):
    """No ``when=`` anywhere: the operation states only what it returns when all is well."""

    __http__ = Http.get("/document", success={200: Bytes(media_type=None)})


class PlainDocuments(Protection):
    download = op(PlainDownload)


def test_conditional_range_beats_unconditional_exact_status() -> None:
    """The whole point of the phase: the service's protection case is no longer shadowed.

    Before this order the success case won on status precision alone and a protection page came
    back as a successful body, with no error raised anywhere.
    """

    with _serve(CHALLENGE) as client, pytest.raises(ChallengeRequired):
        PlainDocuments(client).download()
    with _serve(BLOCKED) as client, pytest.raises(AccessBlocked):
        PlainDocuments(client).download()


def test_the_same_declaration_still_returns_the_ordinary_body() -> None:
    """A real payload does not satisfy either condition, so those cases are not candidates."""

    with _serve(PDF, media="application/pdf") as client:
        assert PlainDocuments(client).download() == PDF


def test_exact_status_still_wins_between_two_conditional_cases() -> None:
    """Once both cases state a criterion, status precision decides as it always did."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Narrow(HttpOperation[bytes]):
        __http__ = Http.get(
            "/document",
            success={200: Bytes(media_type=None, when=lambda _context: True)},
        )

    class Service(Protection):
        download = op(Narrow)

    # The success case is conditional too, so its exact 200 outranks the service's range.
    with _serve(CHALLENGE) as client:
        assert Service(client).download() == CHALLENGE


def test_unconditional_cases_keep_status_order() -> None:
    """Between two cases that state nothing, the exact status is still the narrower one."""

    from eazy_sdk.response.cases import DefaultStatus

    exact: Error[Any] = Error(404, Text(media_type=None), exception=ChallengeRequired)
    ranged: Error[Any] = Error(
        StatusRange(400, 499), Text(media_type=None), exception=AccessBlocked
    )
    fallback: Error[Any] = Error(DefaultStatus(), Text(media_type=None), exception=AccessBlocked)
    assert _specificity(exact) > _specificity(ranged) > _specificity(fallback)


def test_operation_still_beats_service_on_a_full_tie() -> None:
    """Precedence stays last: it breaks a tie, it never overtakes a criterion."""

    operation: Error[Any] = Error(
        404, Text(media_type=None), exception=ChallengeRequired, precedence=0
    )
    service: Error[Any] = Error(404, Text(media_type=None), exception=AccessBlocked, precedence=1)
    assert _specificity(operation) > _specificity(service)


def test_specificity_order_is_criterion_status_media_layer() -> None:
    """The order itself, read straight off the tuple, so a reshuffle cannot pass unnoticed."""

    conditional_range: Error[Any] = Error(
        StatusRange(200, 599),
        Text(media_type=None),
        exception=ChallengeRequired,
        condition=is_challenge,
        precedence=1,
    )
    plain_exact: Success[Any] = Success(200, Bytes(media_type=None))

    assert _specificity(conditional_range) == (1, 1, 0, -1)
    assert _specificity(plain_exact) == (0, 2, 0, 0)
    assert _specificity(conditional_range) > _specificity(plain_exact)


def test_an_operation_overrides_a_service_condition_with_its_own() -> None:
    """The documented escape hatch: the operation states its own criterion and wins on status."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Odd(HttpOperation[bytes]):
        __http__ = Http.get(
            "/document",
            success={200: Bytes(media_type=None, when=is_challenge)},
        )

    class Service(Protection):
        download = op(Odd)

    with _serve(CHALLENGE) as client:
        assert Service(client).download() == CHALLENGE


# --- 53.3: ``accept=`` decides on the parsed value ---------------------------------------------


@dataclass(frozen=True)
class Page:
    number: int


class RequestFailed(ApiError[Any]):
    """The business failure a 200 can carry, now an ordinary error case."""


@dataclass(frozen=True)
class PlainEnvelope:
    """A service envelope with no rule of its own: the case decides, through ``accept=``."""

    Result: Page | None
    Success: bool
    Message: str | None = None


ENVELOPE_OK = b'{"Result": {"number": 7}, "Success": true, "Message": null}'
ENVELOPE_FAILED = b'{"Result": null, "Success": false, "Message": "not found"}'


def test_accept_decides_on_the_parsed_value() -> None:
    """The criterion the raw body cannot answer: a business status inside a 200."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[PlainEnvelope]):
        __http__ = Http.get(
            "/page",
            success={200: Json(PlainEnvelope, accept=lambda r: r.Success)},
            errors={200: (Json(PlainEnvelope, accept=lambda r: not r.Success), RequestFailed)},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with _serve(ENVELOPE_OK, media="application/json") as client:
        assert Service(client).fetch().Result == Page(number=7)
    with (
        _serve(ENVELOPE_FAILED, media="application/json") as client,
        pytest.raises(RequestFailed) as raised,
    ):
        Service(client).fetch()
    assert raised.value.error.Message == "not found"


def test_accept_is_not_inverted_on_an_error_case() -> None:
    """``accept=`` answers "does this case match", so it reads as written on both kinds."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[PlainEnvelope]):
        __http__ = Http.get(
            "/page",
            # Both cases ask for the same answer, so the successful body is claimed by both.
            success={200: Json(PlainEnvelope, accept=lambda r: r.Success)},
            errors={200: (Json(PlainEnvelope, accept=lambda r: r.Success), RequestFailed)},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with (
        _serve(ENVELOPE_OK, media="application/json") as client,
        pytest.raises(AmbiguousResponseError),
    ):
        Service(client).fetch()


def test_exception_inside_accept_is_malformed() -> None:
    """A broken declaration meeting a real body is a malformed response, not a crashed call."""

    def explode(_value: PlainEnvelope) -> bool:
        raise RuntimeError("boom")

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[PlainEnvelope]):
        __http__ = Http.get("/page", success={200: Json(PlainEnvelope, accept=explode)})

    class Service(SyncApi):
        fetch = op(Fetch)

    with (
        _serve(ENVELOPE_OK, media="application/json") as client,
        pytest.raises(MalformedResponseError),
    ):
        Service(client).fetch()


def test_a_case_with_accept_ranks_as_conditional() -> None:
    plain: Success[Any] = Success(200, Json(PlainEnvelope))
    with_accept: Success[Any] = Success(200, Json(PlainEnvelope, accept=lambda _r: True))
    assert _specificity(plain)[0] == 0
    assert _specificity(with_accept)[0] == 1


# --- 53.4: composable predicates over the raw response ----------------------------------------


def _context(
    payload: bytes,
    media: str = "text/html",
    code: int = 200,
    headers: tuple[tuple[str, str], ...] = (),
) -> ResponseContext[object]:
    """A response without a call: a predicate reads the response and nothing else."""

    response: NormalizedResponse[object] = NormalizedResponse(
        code,
        "https://api.example/thing",
        "GET",
        (("Content-Type", media), *headers),
        payload,
    )
    return ResponseContext(response)


def test_body_predicates_read_bytes_or_text() -> None:
    """A bytes argument reads the raw body, a str argument reads the decoded text."""

    assert match.body.startswith(b"%PDF-")(_context(PDF))
    assert not match.body.startswith(b"%PDF-")(_context(CHALLENGE))
    assert match.body.contains("pravocaptcha")(_context(b"<html>pravocaptcha</html>"))
    assert match.body.contains(b"CAPTCHA", ignore_case=True)(_context(b"<i>captcha</i>"))
    assert match.body.contains("КАПЧА", ignore_case=True)(_context("КаПчА".encode()))
    assert match.body.is_empty()(_context(b""))
    assert not match.body.is_empty()(_context(PDF))


def test_str_argument_on_an_undecodable_body_is_false() -> None:
    """A routing decision on a body that will not decode is a no, never a crashed call."""

    undecodable = _context(b"\xff\xfe\x00 not utf-8", media="text/html; charset=utf-8")
    assert match.body.contains("anything")(undecodable) is False
    assert match.body.startswith("anything")(undecodable) is False
    # The same marker as bytes still reads the raw body.
    assert match.body.contains(b"not utf-8")(undecodable)


def test_content_type_status_and_header_predicates() -> None:
    assert match.content_type.is_("text/html")(_context(PDF, media="text/html; charset=utf-8"))
    assert match.content_type.startswith("text/")(_context(PDF))
    assert not match.content_type.startswith("application/")(_context(PDF))
    assert match.status.is_(200)(_context(PDF))
    assert match.status.in_(200, 299)(_context(PDF, code=204))
    assert not match.status.in_(200, 299)(_context(PDF, code=404))
    located = _context(PDF, headers=(("Location", "/next"),))
    assert match.header("Location").present()(located)
    assert match.header("Location").is_("/next")(located)
    assert match.header("Location").contains("nex")(located)
    assert not match.header("Retry-After").present()(located)


def test_predicates_compose_with_and_or_not() -> None:
    """The four kad predicates, written as expressions instead of four functions."""

    is_pdf_body = match.body.startswith(b"%PDF-")
    is_captcha = match.body.contains(b"pravocaptcha") | match.body.contains(b"recaptchatoken")
    is_denied = match.body.contains(b"support@example.test")
    is_regular = ~(is_captcha | is_denied) & match.content_type.startswith("text/html")

    assert is_pdf_body(_context(PDF))
    assert is_captcha(_context(b"<html>recaptchatoken</html>"))
    assert is_denied(_context(BLOCKED))
    assert is_regular(_context(b"<html>an ordinary page</html>"))
    assert not is_regular(_context(CHALLENGE))
    assert not is_regular(_context(PDF, media="application/pdf"))


def test_a_predicate_mixes_with_a_plain_lambda() -> None:
    """Sugar over ``ResponseCondition``, not a second type: the two combine either way."""

    combined = match.body.startswith(b"%PDF-") & (
        lambda context: context.response.status_code == 200
    )
    assert combined(_context(PDF))
    assert not combined(_context(PDF, code=500))


def test_predicate_label_reads_in_a_repr() -> None:
    """A diagnostic names the criteria that were weighed, not a row of <lambda>."""

    predicate = ~(match.body.startswith(b"%PDF-") | match.status.is_(204))
    assert repr(predicate) == "<not (body startswith b'%PDF-' or status is 204)>"
    assert match.body.contains("x", ignore_case=True).label == "body contains 'x' ignoring case"


def test_body_matches_a_compiled_pattern() -> None:
    import re

    assert match.body.matches(re.compile(rb"%PDF-\d"))(_context(PDF))
    assert match.body.matches(re.compile(r"pravo\w+"))(_context(CHALLENGE))
    assert not match.body.matches(re.compile(r"nothing here"))(_context(CHALLENGE))


def test_a_predicate_serves_as_a_case_condition() -> None:
    """The point of the module: it is written straight into ``when=``."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[bytes]):
        __http__ = Http.get(
            "/document",
            success={200: Bytes(media_type=None, when=match.body.startswith(b"%PDF-"))},
            errors={
                200: (
                    Text(media_type=None, when=match.body.contains(b"pravocaptcha")),
                    ChallengeRequired,
                )
            },
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with _serve(PDF, media="application/pdf") as client:
        assert Service(client).fetch() == PDF
    with _serve(CHALLENGE) as client, pytest.raises(ChallengeRequired):
        Service(client).fetch()


# --- 53.5: what the declaration alone already proves wrong ------------------------------------


def test_one_model_on_both_sides_with_nothing_to_tell_them_apart_is_refused() -> None:
    """The narrow half of the shadowing check: these two cases genuinely cannot be told apart."""

    @dataclass(frozen=True)
    class Plain:
        Result: Page | None
        Success: bool

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Plain]):
        __http__ = Http.get(
            "/page",
            success={200: Json(Plain)},
            errors={200: (Json(Plain), RequestFailed)},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with pytest.raises(PlanError, match="is declared for both success and error on status 200"):
        Service(_serve(ENVELOPE_OK)).fetch()


def test_the_ordinary_service_pairing_is_not_refused() -> None:
    """The check must never fire on the pattern phase 53 exists to support."""

    with _serve(CHALLENGE) as client, pytest.raises(ChallengeRequired):
        PlainDocuments(client).download()


def test_accept_on_one_case_also_tells_the_pair_apart() -> None:
    @dataclass(frozen=True)
    class Plain:
        Result: Page | None
        Success: bool

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Plain]):
        __http__ = Http.get(
            "/page",
            success={200: Json(Plain, accept=lambda r: r.Success)},
            errors={200: (Json(Plain), RequestFailed)},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with _serve(ENVELOPE_OK, media="application/json") as client:
        assert Service(client).fetch().Success is True


