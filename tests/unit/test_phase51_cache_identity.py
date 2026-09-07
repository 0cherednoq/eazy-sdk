"""Review of phase 50: caches keyed by ``id()`` of an object the cache does not hold.

Two of them existed. ``ResponseContext.cached`` keyed by ``id()`` of a tuple built at the call
site, so the entry was never found again, the parsed document was rebuilt for every candidate
case, and the freed address stayed a live key. ``ExecutionCore._with_client_errors`` keyed by
``id(contract)`` while keeping no reference to that contract, so a rebuilt router could inherit
another operation's declaration. Both now key by value, which is what makes the entry outlive
its key safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import pytest
from eazy_sdk_html import CSS, ParselBackend
from eazy_sdk_xml import ElementTreeBackend, ElementTreeXmlCodec

from eazy_sdk import Client, ClientConfig, SyncApi, api
from eazy_sdk.compile.http_operation import _OperationDeclaration
from eazy_sdk.compile.input import MethodInputSchema
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import ApiError, ResponseContext
from eazy_sdk.response.cases import HTML_EXTRACTOR, Error, Html, Json, Responses, Success
from eazy_sdk.response.normalized import NormalizedResponse
from eazy_sdk.serialization import Serialization

BASE = "https://books.example"

PAGE = b"<html><body><h1 class='title'>Dune</h1></body></html>"


@dataclass(frozen=True)
class Title:
    title: Annotated[str, CSS("h1.title::text")]


@dataclass(frozen=True)
class Missing:
    missing: Annotated[str, CSS("h2.absent::text")]


def _response(body: bytes = PAGE, *, content_type: str = "text/html") -> ResponseContext[object]:
    raw = httpx.Response(200, content=body, headers={"content-type": content_type})
    return ResponseContext(
        NormalizedResponse(
            status_code=raw.status_code,
            url=f"{BASE}/thing",
            method="GET",
            headers=tuple(raw.headers.items()),
            body=raw.content,
            raw_response=raw,
        ),
        serialization=Serialization(documents=(ParselBackend(),)),
    )


# --- the response artifact cache ------------------------------------------------------


def test_document_is_parsed_once_for_every_candidate_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arbitration tries each candidate model; the page behind them is parsed once."""

    import eazy_sdk_html

    builds = 0
    original = eazy_sdk_html.HtmlDocument

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal builds
        builds += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(eazy_sdk_html, "HtmlDocument", counted)

    context = _response()
    bound = HTML_EXTRACTOR.bind(context)
    bound.extract(Missing)
    bound.extract(Title)
    bound.extract(Title)

    assert builds == 1, "three extractions of one response parsed the document three times"
    assert len(context._artifacts) == 1


def test_cache_entries_are_told_apart_by_backend() -> None:
    """Two parsers of one response are two artifacts, not one shared by accident."""

    context = _response()
    parsel = context.cached((HTML_EXTRACTOR, ParselBackend()), lambda: "parsel")
    elementtree = context.cached((HTML_EXTRACTOR, ElementTreeBackend()), lambda: "elementtree")

    assert (parsel, elementtree) == ("parsel", "elementtree")
    assert len(context._artifacts) == 2


def test_cache_key_is_held_by_value() -> None:
    """Equal keys are one entry, and the key stays alive because the dict holds it."""

    context = _response()
    calls = 0

    def build() -> str:
        nonlocal calls
        calls += 1
        return "parsed"

    assert context.cached(ElementTreeXmlCodec(), build) == "parsed"
    assert context.cached(ElementTreeXmlCodec(), build) == "parsed"
    assert calls == 1
    assert len(context._artifacts) == 1
    assert all(not isinstance(key, int) for key in context._artifacts), (
        "an id() key outlives what it stands for and can be handed to another object"
    )


# --- the client-error contract cache --------------------------------------------------


@dataclass(frozen=True)
class Problem:
    detail: str


class Refused(ApiError[Problem]):
    pass


class Blocked(ApiError[Problem]):
    pass


def _declaration(operation_id: str, *, errors: tuple[Error[Any], ...] = ()) -> Any:
    return _OperationDeclaration[Any](
        operation_id=operation_id,
        method="GET",
        path="/thing",
        input_fields=(),
        input_schema=MethodInputSchema(()),
        result_type=Title,
        responses=Responses(success=(Success(200, Html(Title)),), errors=errors),
    )


def _core() -> Any:
    from eazy_sdk.clients.executor import ExecutionCore, ExecutionRuntime
    from eazy_sdk.handlers.profile import HandlerProfile
    from eazy_sdk.request.prepared import HttpProtocol

    def send(*args: object, **kwargs: object) -> object:
        raise AssertionError("the cache test never sends")

    runtime = ExecutionRuntime(
        handler_profile=HandlerProfile(protocols=frozenset({HttpProtocol.HTTP_1_1})),
        send=send,
        errors={"books.example": {404: Refused}},
    )
    return ExecutionCore(runtime)


def test_client_error_contracts_are_keyed_by_operation_and_host() -> None:
    """Two operations, one host: two entries, each derived from its own declaration."""

    core = _core()
    first = _declaration("first")
    second = _declaration("second")

    derived_first = core._with_client_errors(first, f"{BASE}/thing")
    derived_second = core._with_client_errors(second, f"{BASE}/thing")

    assert core._with_client_errors(first, f"{BASE}/thing") is derived_first, "cache never hit"
    assert derived_first is not derived_second
    assert derived_first.operation_id == "first"
    assert derived_second.operation_id == "second"
    assert set(core._client_error_contracts) == {
        ("first", "books.example"),
        ("second", "books.example"),
    }


def test_rebuilt_declaration_does_not_inherit_the_previous_contract() -> None:
    """A router rebuilt per session declares the same operation again: it derives its own."""

    core = _core()
    original = _declaration("first", errors=(Error(403, Json(Problem), Blocked),))
    core._with_client_errors(original, f"{BASE}/thing")

    rebuilt = _declaration("first")
    derived = core._with_client_errors(rebuilt, f"{BASE}/thing")

    statuses = {case.status for case in derived.responses.errors}
    assert statuses == {404}, "the rebuilt operation inherited the previous declaration's cases"
    assert len(core._client_error_contracts) == 1, "one operation and one host is one entry"


def test_client_errors_still_apply_by_host() -> None:
    """The behaviour the cache serves is unchanged: the host's case reaches the caller."""

    def serve(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            content=b'{"detail": "no such book"}',
            headers={"content-type": "application/json"},
        )

    raw = httpx.Client(transport=httpx.MockTransport(serve), headers={}, cookies={})
    client = Client(
        base_url=BASE,
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(errors={"books.example": {404: Refused}}),
    )

    class Books(SyncApi):
        @api.get("/thing")
        def get(self) -> Title:
            raise NotImplementedError

    with client:
        sdk = Books(client)
        for _ in range(3):
            with pytest.raises(Refused):
                sdk.get()
