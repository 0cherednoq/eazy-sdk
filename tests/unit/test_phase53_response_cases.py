"""Phase 53: one style for response cases, and a condition that outranks status precision.

The scenario throughout is the one that motivated the phase: a service answers 200 with either
the real payload or a protection page, and the protection cases are declared once for the whole
service. Nothing about that should force every operation to repeat the negation of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict, cast

import httpx
import pytest

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import (
    ApiError,
    Bytes,
    Empty,
    Envelope,
    Error,
    Extracted,
    Html,
    Json,
    MalformedResponseError,
    Parsed,
    ResponseContext,
    ResponseExtractor,
    StatusRange,
    Success,
    Text,
)
from eazy_sdk.response.cases import (
    ResponseParser,
    _specificity,
    envelope_of,
    envelope_payload_type,
)

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


# --- 53.3: the envelope is declared on the model ----------------------------------------------


@dataclass(frozen=True)
class Page:
    """The payload a caller actually wants; the envelope around it is a transport detail."""

    number: int


class RequestFailed(ApiError[Any]):
    """The business failure a 200 can carry, now an ordinary error case."""


@dataclass(frozen=True)
class DataclassEnvelope:
    Result: Page | None
    Success: bool
    Message: str | None = None

    __envelope__ = Envelope(succeeds=lambda r: r.Success, payload=lambda r: r.Result)


ENVELOPE_OK = b'{"Result": {"number": 7}, "Success": true, "Message": null}'
ENVELOPE_FAILED = b'{"Result": null, "Success": false, "Message": "not found"}'


def _envelope_service(model: Any) -> Any:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Page]):
        __http__ = Http.get(
            "/page",
            success={200: Json(model)},
            errors={200: (Json(model), RequestFailed)},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    return Service


def test_envelope_splits_success_and_failure_on_one_status() -> None:
    """One predicate on the model declares both halves of a 200-with-business-status service."""

    service = _envelope_service(DataclassEnvelope)
    with _serve(ENVELOPE_OK, media="application/json") as client:
        assert service(client).fetch() == Page(number=7)
    with (
        _serve(ENVELOPE_FAILED, media="application/json") as client,
        pytest.raises(RequestFailed) as raised,
    ):
        service(client).fetch()
    assert raised.value.error.Message == "not found"


def test_envelope_payload_becomes_the_operation_result() -> None:
    """The error keeps the whole envelope; only the success is projected to the payload."""

    service = _envelope_service(DataclassEnvelope)
    with _serve(ENVELOPE_OK, media="application/json") as client:
        result = service(client).fetch()
    assert isinstance(result, Page)
    assert not hasattr(result, "Success")


def test_envelope_works_on_every_model_backend() -> None:
    """I9: a class attribute is the one declaration form all four libraries support."""

    import msgspec
    from pydantic import BaseModel

    class PydanticEnvelope(BaseModel):
        Result: Page | None
        Success: bool
        Message: str | None = None

        __envelope__ = Envelope(succeeds=lambda r: r.Success, payload=lambda r: r.Result)

    class MsgspecEnvelope(msgspec.Struct):
        Result: Page | None
        Success: bool
        Message: str | None = None

        __envelope__ = Envelope(succeeds=lambda r: r.Success, payload=lambda r: r.Result)

    class TypedDictEnvelope(TypedDict):
        Result: Page | None
        Success: bool
        Message: str | None

        # A TypedDict value is a plain dict, so the rule reads keys, not attributes. That is
        # exactly why the declaration is an attribute and never a method.
        __envelope__ = Envelope(  # type: ignore[misc]
            succeeds=lambda r: r["Success"],
            payload=lambda r: r["Result"],
        )

    for model in (DataclassEnvelope, PydanticEnvelope, MsgspecEnvelope, TypedDictEnvelope):
        service = _envelope_service(model)
        with _serve(ENVELOPE_OK, media="application/json") as client:
            assert service(client).fetch() == Page(number=7), model
        with (
            _serve(ENVELOPE_FAILED, media="application/json") as client,
            pytest.raises(RequestFailed),
        ):
            service(client).fetch()


def test_envelope_inherited_from_a_base_class() -> None:
    """A service declares the rule once; every envelope of that service inherits it."""

    @dataclass(frozen=True)
    class Base:
        __envelope__ = Envelope(succeeds=lambda r: r.Success, payload=lambda r: r.Result)

    @dataclass(frozen=True)
    class Inherited(Base):
        Result: Page | None
        Success: bool
        Message: str | None = None

    service = _envelope_service(Inherited)
    with _serve(ENVELOPE_OK, media="application/json") as client:
        assert service(client).fetch() == Page(number=7)


@dataclass(frozen=True)
class StatusOnly:
    """An envelope that says when it succeeded but names no payload."""

    Result: Page | None
    Success: bool
    Message: str | None = None

    __envelope__ = Envelope(succeeds=lambda r: r.Success)


def test_accept_overrides_the_model_rule() -> None:
    """``accept=`` answers a different question, so it is read as written and wins."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[StatusOnly]):
        __http__ = Http.get(
            "/page",
            success={200: Json(StatusOnly, accept=lambda r: r.Message == "not found")},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    # The envelope calls this a failure; ``accept=`` says this case wants it anyway.
    with _serve(ENVELOPE_FAILED, media="application/json") as client:
        assert Service(client).fetch().Message == "not found"


def test_payload_applies_whichever_criterion_decided() -> None:
    """``accept=`` replaces the criterion only; where the payload sits is still the model's."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Page]):
        __http__ = Http.get(
            "/page",
            success={200: Json(DataclassEnvelope, accept=lambda _r: True)},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with _serve(ENVELOPE_OK, media="application/json") as client:
        assert Service(client).fetch() == Page(number=7)


def test_accept_is_not_inverted_on_an_error_case() -> None:
    """``accept=`` on an error case matches when it returns True, never when it returns False."""

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Page]):
        __http__ = Http.get(
            "/page",
            success={200: Json(DataclassEnvelope, accept=lambda r: r.Success)},
            errors={200: (Json(DataclassEnvelope, accept=lambda r: not r.Success), RequestFailed)},
        )

    class Service(SyncApi):
        fetch = op(Fetch)

    with (
        _serve(ENVELOPE_FAILED, media="application/json") as client,
        pytest.raises(RequestFailed),
    ):
        Service(client).fetch()


def test_exception_in_succeeds_is_malformed() -> None:
    """A broken author callable meeting a real body is Malformed, not a crashed call."""

    def explode(_envelope: object) -> bool:
        raise KeyError("Succes")

    @dataclass(frozen=True)
    class Broken:
        Result: Page | None
        Success: bool
        Message: str | None = None

        __envelope__ = Envelope(succeeds=explode)

    @dataclass(frozen=True, slots=True, kw_only=True)
    class Fetch(HttpOperation[Broken]):
        __http__ = Http.get("/page", success={200: Json(Broken)})

    class Service(SyncApi):
        fetch = op(Fetch)

    with (
        _serve(ENVELOPE_OK, media="application/json") as client,
        pytest.raises(MalformedResponseError),
    ):
        Service(client).fetch()


def test_a_case_with_an_envelope_ranks_as_conditional() -> None:
    """I3: all three spellings of a criterion carry the same rank."""

    plain: Success[Any] = Success(200, Json(Page))
    with_envelope: Success[Any] = Success(200, Json(DataclassEnvelope))
    with_accept: Success[Any] = Success(200, Json(Page, accept=lambda _value: True))

    assert _specificity(plain)[0] == 0
    assert _specificity(with_envelope)[0] == 1
    assert _specificity(with_accept)[0] == 1


def test_envelope_must_be_an_envelope() -> None:
    @dataclass(frozen=True)
    class Wrong:
        Result: Page | None
        Success: bool

        __envelope__: ClassVar[Any] = {"succeeds": "yes"}

    with pytest.raises(PlanError, match=r"Wrong\.__envelope__ must be an Envelope, got dict"):
        envelope_of(Wrong)


def test_envelope_declares_at_least_one_rule() -> None:
    with pytest.raises(ValueError, match="declares neither succeeds= nor payload="):
        Envelope()


def test_envelope_rules_must_be_callable() -> None:
    with pytest.raises(TypeError, match=r"Envelope\(succeeds=\) must be callable"):
        Envelope(succeeds=cast(Any, "yes"))
    with pytest.raises(TypeError, match=r"Envelope\(payload=\) must be callable"):
        Envelope(payload=cast(Any, "there"))


def test_annotated_payload_declares_the_result_type() -> None:
    """An annotated projection states the result type; an unannotated lambda states nothing."""

    def to_page(envelope: DataclassEnvelope) -> Page | None:
        return envelope.Result

    @dataclass(frozen=True)
    class WithAnnotation:
        Result: Page | None
        Success: bool

        __envelope__ = Envelope(succeeds=lambda r: r.Success, payload=to_page)

    assert envelope_payload_type(WithAnnotation) == (Page | None)
    assert envelope_payload_type(DataclassEnvelope) is None
    assert envelope_payload_type(Page) is None
