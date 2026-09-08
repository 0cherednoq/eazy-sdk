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
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import (
    ApiError,
    Bytes,
    Empty,
    Error,
    Extracted,
    Html,
    Json,
    Parsed,
    ResponseContext,
    ResponseExtractor,
    StatusRange,
    Text,
)
from eazy_sdk.response.cases import ResponseParser

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
    from eazy_sdk.core.errors import PlanError

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
