"""Phase 50.2: the response family comes from the model, never from the declaration site.

The four shapes measured in ``experiments/response_case_sugar/j_html_service.py`` are the
whole question: an HTML page, an HTML page nested inside another, an HTML error page on a
JSON service, and a JSON problem on an HTML service. None of them says ``Html(...)`` or
``Json(...)`` at the operation — the fields of the model already say which family reads it.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast

import httpx
import msgspec
import pytest
from eazy_sdk_html import CSS, Scope
from pydantic import BaseModel

from eazy_sdk import (
    Client,
    ClientConfig,
    Http,
    HttpOperation,
    JsonField,
    Path,
    SyncApi,
    api,
    op,
)
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.models import default_model_adapters
from eazy_sdk.models.documents import is_document_model
from eazy_sdk.protocols import JsonRpc
from eazy_sdk.request import (
    SigningKeyRequirement,
    canonical_json,
    header_output,
    hmac_sha256,
    markers,
)
from eazy_sdk.response import (
    DEFAULT,
    AmbiguousResponseError,
    ApiError,
    ErrorSummary,
    Html,
    Json,
    MalformedResponseError,
    Responses,
    UnexpectedResponseError,
)
from eazy_sdk.response._mapping import representation
from eazy_sdk.response.cases import resolve_json_pointer
from eazy_sdk.serialization import BackendCapabilityError

BASE = "https://books.example"


# --- the models, as the experiment declared them -------------------------------------


@dataclass(frozen=True, slots=True)
class BookCard:
    title: Annotated[str, CSS("h3 a::attr(title)")]
    price: Annotated[str, CSS("p.price_color::text")]


@dataclass(frozen=True, slots=True)
class CatalogPage:
    """A document twice over: its own selector, and a nested model behind ``Scope``."""

    title: Annotated[str, CSS("h1::text")]
    books: Annotated[list[BookCard], Scope(CSS("article.product_pod"))]


@dataclass(frozen=True, slots=True)
class NotFoundPage:
    heading: Annotated[str, CSS("h1::text")]


@dataclass(frozen=True, slots=True)
class GatewayPage:
    heading: Annotated[str, CSS("h1::text")]


class ApiProblem(BaseModel):
    """A JSON problem document, on a service that otherwise serves HTML."""

    code: str
    message: str


class OrderPayload(BaseModel):
    id: str
    total: int


@dataclass(frozen=True, slots=True)
class Nested:
    """No selector of its own: the document is the model it holds."""

    page: CatalogPage


class PageNotFound(ApiError[NotFoundPage]):
    pass


class RateLimited(ApiError[ApiProblem]):
    pass


class GatewayDown(ApiError[GatewayPage]):
    pass


# --- the service ---------------------------------------------------------------------

CATALOG_HTML = b"""
<html><body>
  <h1>Fiction</h1>
  <article class="product_pod">
    <h3><a title="A Light in the Attic"></a></h3>
    <p class="price_color">51.77</p>
  </article>
</body></html>
"""
NOT_FOUND_HTML = b"<html><body><h1>Page not found</h1></body></html>"
GATEWAY_HTML = b"<html><body><h1>502 Bad Gateway</h1></body></html>"


def _serve(request: httpx.Request) -> httpx.Response:
    html = {"content-type": "text/html; charset=utf-8"}
    path = request.url.path
    if path.endswith("page-1.html"):
        return httpx.Response(200, content=CATALOG_HTML, headers=html)
    if path.endswith("page-99.html"):
        return httpx.Response(404, content=NOT_FOUND_HTML, headers=html)
    if path.endswith("page-429.html"):
        return httpx.Response(429, json={"code": "slow_down", "message": "Too many requests"})
    if path.endswith("/orders/order-42"):
        return httpx.Response(200, json={"id": "order-42", "total": 12_900})
    return httpx.Response(502, content=GATEWAY_HTML, headers=html)


def _client() -> Client:
    raw = httpx.Client(transport=httpx.MockTransport(_serve), headers={}, cookies={})
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True))


@dataclass(frozen=True, slots=True, kw_only=True)
class GetPage(HttpOperation[CatalogPage]):
    __http__ = Http.get(
        "/catalogue/page-{page}.html",
        operation_id="getCatalogPage",
        errors={404: PageNotFound, 429: RateLimited},
    )
    page: Path[int]


@dataclass(frozen=True, slots=True, kw_only=True)
class GetOrder(HttpOperation[OrderPayload]):
    __http__ = Http.get(
        "/orders/{order_id}",
        operation_id="getOrder",
        errors={502: GatewayDown},
    )
    order_id: Path[str]


class BooksApi(SyncApi):
    get_page = op(GetPage)
    get_order = op(GetOrder)


def test_document_model_is_read_by_html_backend_without_declaration() -> None:
    """The four cases of ``j_html_service.py``, with no ``Html``/``Json`` on any operation."""

    with _client() as client:
        books = BooksApi(client)

        page = books.get_page(page=1)
        assert (page.title, page.books[0].title, page.books[0].price) == (
            "Fiction",
            "A Light in the Attic",
            "51.77",
        )

        with pytest.raises(PageNotFound) as not_found:
            books.get_page(page=99)
        assert not_found.value.error.heading == "Page not found"

        with pytest.raises(RateLimited) as limited:
            books.get_page(page=429)
        assert limited.value.error.message == "Too many requests"

        assert books.get_order(order_id="order-42").total == 12_900
        with pytest.raises(GatewayDown) as gateway:
            books.get_order(order_id="down")
        assert gateway.value.error.heading == "502 Bad Gateway"


def test_structural_model_stays_json() -> None:
    """A model without selectors is a structure, whichever side of the call it is on."""

    models = default_model_adapters()
    assert not is_document_model(ApiProblem, models)
    assert not is_document_model(OrderPayload, models)
    assert not is_document_model(list[OrderPayload], models)
    assert isinstance(representation(ApiProblem, models=models), Json)

    cases = cast(Responses[Any], BooksApi.get_order.declaration.responses)
    assert isinstance(cases.success[0].response, Json)
    assert isinstance(cases.errors[0].response, Html), "the 502 page is a document"


def test_nested_document_model_detected() -> None:
    """The selectors may sit one, two or three models deep; the answer is the same."""

    models = default_model_adapters()
    assert is_document_model(CatalogPage, models)
    assert is_document_model(BookCard, models)
    assert is_document_model(Nested, models)
    assert is_document_model(list[Nested] | None, models)
    assert isinstance(representation(CatalogPage, models=models), Html)

    cases = cast(Responses[Any], BooksApi.get_page.declaration.responses)
    families: dict[Any, str] = {case.status: type(case.response).__name__ for case in cases.success}
    families.update({case.status: type(case.response).__name__ for case in cases.errors})
    assert families == {200: "Html", 404: "Html", 429: "Json"}


# --- 50.2.2 specificity and layers ---------------------------------------------------


class Alpha(BaseModel):
    """Two shapes that both accept the same body: only the declaration tells them apart."""

    kind: str = "alpha"


class Beta(BaseModel):
    kind: str = "beta"


class AlphaFailed(ApiError[Alpha]):
    pass


class BetaFailed(ApiError[Beta]):
    pass


def _echo(
    status: int, media: str = "application/json"
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"{}", headers={"content-type": media})

    return handler


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> Client:
    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(base_url=BASE, handler=HttpxHandler(raw, owns_client=True))


def test_exact_status_beats_range() -> None:
    """404 and "4xx" both match a 404; the exact status is the more specific declaration."""

    class Service(SyncApi):
        @api.get("/thing", errors={404: AlphaFailed, "4xx": BetaFailed})
        def get(self) -> Alpha:
            raise NotImplementedError

    with _mock_client(_echo(404)) as client, pytest.raises(AlphaFailed):
        Service(client).get()


def test_range_beats_default_fallback() -> None:
    """A range is narrower than ``DEFAULT``, even when both parse the body."""

    class Service(SyncApi):
        @api.get("/thing", success={"2xx": Alpha, DEFAULT: Beta})
        def get(self) -> Alpha | Beta:
            raise NotImplementedError

    with _mock_client(_echo(200)) as client:
        assert Service(client).get().kind == "alpha"


def test_explicit_media_beats_wildcard() -> None:
    """``application/json`` is more specific than ``*/*`` for the same status."""

    class Service(SyncApi):
        @api.get("/thing", success={200: [Json(Alpha), Json(Beta, media_type="*/*")]})
        def get(self) -> Alpha | Beta:
            raise NotImplementedError

    with _mock_client(_echo(200)) as client:
        assert Service(client).get().kind == "alpha"


def test_operation_case_beats_service_case() -> None:
    """The layer nearest the operation wins: operation, then service, then client."""

    class Service(SyncApi):
        errors = ({404: BetaFailed},)

        @api.get("/thing", errors={404: AlphaFailed})
        def get(self) -> Alpha:
            raise NotImplementedError

        @api.get("/other")
        def other(self) -> Alpha:
            raise NotImplementedError

    with _mock_client(_echo(404)) as client:
        service = Service(client)
        with pytest.raises(AlphaFailed):
            service.get()
        with pytest.raises(BetaFailed):
            service.other()


def test_true_tie_is_ambiguous() -> None:
    """Two equally specific cases stay ambiguous: nothing in the declaration decides."""

    class Service(SyncApi):
        @api.get("/thing", success={200: [Json(Alpha), Json(Beta)]})
        def get(self) -> Alpha | Beta:
            raise NotImplementedError

    with _mock_client(_echo(200)) as client, pytest.raises(AmbiguousResponseError):
        Service(client).get()


# --- 50.2.3 the (Model, factory) form and the diagnostics ----------------------------


class Refused(Exception):
    """An application exception that owes the library nothing — not even a base class."""

    def __init__(self, problem: Alpha, context: object) -> None:
        self.problem = problem
        self.context = context
        super().__init__(problem.kind)


def test_tuple_factory_raises_application_exception_without_apierror() -> None:
    """``errors={404: (Model, factory)}`` keeps the exception outside the library."""

    class Service(SyncApi):
        @api.get("/thing", errors={404: (Alpha, Refused)})
        def get(self) -> Alpha:
            raise NotImplementedError

    with _mock_client(_echo(404)) as client, pytest.raises(Refused) as refused:
        Service(client).get()
    assert refused.value.problem.kind == "alpha"
    assert not isinstance(refused.value, ApiError)


def test_default_key_in_errors_rejected() -> None:
    """D-15: ``DEFAULT`` in ``errors=`` is the ``fallback=`` parameter, spelled wrongly."""

    with pytest.raises(PlanError) as failure:

        class Service(SyncApi):
            @api.get("/thing", errors={DEFAULT: AlphaFailed})
            def get(self) -> Alpha:
                raise NotImplementedError

    assert "cannot use DEFAULT; declare fallback= instead" in str(failure.value)


def test_apierror_without_model_rejected() -> None:
    """D-14: an ``ApiError`` subclass that names no model cannot say what it parses."""

    class Nameless(ApiError):  # type: ignore[type-arg]
        pass

    with pytest.raises(PlanError) as failure:

        class Service(SyncApi):
            @api.get("/thing", errors={404: Nameless})
            def get(self) -> Alpha:
                raise NotImplementedError

    assert str(failure.value) == (
        "Nameless does not name a problem model; declare "
        "class Nameless(ApiError[Model]) or use (Model, factory)"
    )


# --- 50.2.4 the service envelope, as a JSON pointer -----------------------------------


class Payload(BaseModel):
    id: str


def _enveloped(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=b'{"data":{"id":"order-42"},"meta":{"trace":"t-1"}}',
        headers={"content-type": "application/json"},
    )


def test_service_unwrap_strips_envelope() -> None:
    """One pointer on the service, and every bare model reads the payload inside it."""

    class Service(SyncApi):
        unwrap = "/data"

        @api.get("/orders")
        def get(self) -> Payload:
            raise NotImplementedError

    with _mock_client(_enveloped) as client:
        assert Service(client).get().id == "order-42"


def test_explicit_json_is_not_unwrapped() -> None:
    """``Json(...)`` written by hand is the whole contract: the service pointer stays out."""

    class Service(SyncApi):
        unwrap = "/data"

        @api.get("/orders", success={200: Json(Payload)})
        def get(self) -> Payload:
            raise NotImplementedError

    with _mock_client(_enveloped) as client, pytest.raises(MalformedResponseError):
        Service(client).get()


def test_unwrap_with_protocol_rejected() -> None:
    """D-17: an envelope already selects the payload; a pointer beside it is two answers."""

    with pytest.raises(TypeError) as failure:

        class Service(SyncApi):
            protocol = JsonRpc(path="/rpc")
            unwrap = "/data"

            @api.get("/orders")
            def get(self) -> Payload:
                raise NotImplementedError

        Service.get.declaration  # noqa: B018 - the service is read on first access

    assert str(failure.value) == (
        "service Service declares both protocol and unwrap; the envelope "
        "already selects the payload"
    )


def test_unwrap_missing_pointer_is_malformed() -> None:
    """A document without the pointed-at path is malformed, not an unexpected response."""

    class Service(SyncApi):
        unwrap = "/payload"

        @api.get("/orders")
        def get(self) -> Payload:
            raise NotImplementedError

    with _mock_client(_enveloped) as client, pytest.raises(MalformedResponseError) as failure:
        Service(client).get()
    assert isinstance(failure.value.__cause__, KeyError)
    assert "/payload" in str(failure.value.__cause__)


def test_json_pointer_walks_objects_and_arrays() -> None:
    """RFC 6901, including the escapes: ``~1`` is a slash and ``~0`` a tilde."""

    document = {"a~b": [{"c/d": 7}]}
    assert resolve_json_pointer(document, "/a~0b/0/c~1d") == 7
    assert resolve_json_pointer(document, "") is document
    for pointer in ("/missing", "/a~0b/9", "/a~0b/0/c~1d/deep"):
        with pytest.raises(KeyError):
            resolve_json_pointer(document, pointer)


# --- 50.2.5 an error that survives leaving the process --------------------------------


def _secret(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        404,
        content=b'{"kind":"alpha","detail":"secret-body"}',
        headers={"content-type": "application/json", "x-trace": "secret-header"},
    )


class SecretApi(SyncApi):
    @api.get("/thing", errors={404: AlphaFailed})
    def get(self) -> Alpha:
        raise NotImplementedError


def test_apierror_pickle_roundtrip() -> None:
    """The exception keeps its type and its parsed error across a process boundary."""

    with _mock_client(_secret) as client, pytest.raises(AlphaFailed) as raised:
        SecretApi(client).get()
    error = raised.value
    assert error.summary == ErrorSummary(
        operation_id="get",
        status_code=404,
        content_type="application/json",
        attempt=1,
        method="GET",
        target="/thing",
    )

    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is AlphaFailed
    assert restored.error == error.error
    assert restored.summary == error.summary
    assert str(restored) == str(error)


def test_pickled_apierror_carries_no_body_or_headers() -> None:
    """What crosses the boundary is the summary: no body, no headers, no live response."""

    with _mock_client(_secret) as client, pytest.raises(AlphaFailed) as raised:
        SecretApi(client).get()
    payload = pickle.dumps(raised.value)
    assert b"secret-body" not in payload
    assert b"secret-header" not in payload
    assert isinstance(pickle.loads(payload).context, ErrorSummary)


# --- 50.2.6 the client layer: what a host answers with --------------------------------


def _host_client(host: str, handler: Callable[[httpx.Request], httpx.Response]) -> Client:
    raw = httpx.Client(transport=httpx.MockTransport(handler), headers={}, cookies={})
    return Client(
        base_url=f"https://{host}",
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(errors={"books.example": {404: BetaFailed}}),
    )


class PlainApi(SyncApi):
    @api.get("/thing")
    def get(self) -> Alpha:
        raise NotImplementedError


def test_client_errors_apply_by_host() -> None:
    """An operation that documents no 404 still raises the client's case for that host."""

    with _host_client("books.example", _echo(404)) as client, pytest.raises(BetaFailed):
        PlainApi(client).get()


def test_client_errors_lose_to_service_and_operation() -> None:
    """The client layer is the outermost: the operation and its service both win a tie."""

    class Service(SyncApi):
        errors = ({404: AlphaFailed},)

        @api.get("/thing")
        def from_service(self) -> Alpha:
            raise NotImplementedError

        @api.get("/thing", errors={404: AlphaFailed}, inherit_errors=False)
        def from_operation(self) -> Alpha:
            raise NotImplementedError

    with _host_client("books.example", _echo(404)) as client:
        service = Service(client)
        with pytest.raises(AlphaFailed):
            service.from_service()
        with pytest.raises(AlphaFailed):
            service.from_operation()


def test_client_errors_ignore_other_hosts() -> None:
    """The key is the exact host: another address gets the undocumented response instead."""

    with (
        _host_client("papers.example", _echo(404)) as client,
        pytest.raises(UnexpectedResponseError),
    ):
        PlainApi(client).get()


# --- 50.2.7 what the response diagnostics say ----------------------------------------


class Catalog(BaseModel):
    """A structural model pointed at an HTML page: the commonest wrong declaration."""

    title: str


def test_unexpected_document_response_hints_selectors() -> None:
    """The message names the reason: an HTML body and a model with no selectors."""

    def serve(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html><h1>Fiction</h1></html>", headers={"content-type": "text/html"}
        )

    class Service(SyncApi):
        @api.get("/catalogue")
        def get(self) -> Catalog:
            raise NotImplementedError

    with _mock_client(serve) as client, pytest.raises(UnexpectedResponseError) as failure:
        Service(client).get()
    assert (
        "the response is a document but none of the attempted models (Catalog) "
        "carries CSS or XPath metadata" in str(failure.value)
    )


@dataclass(frozen=True, slots=True)
class BlockPage:
    """Everything optional: this model extracts from a catalogue and an error page alike."""

    heading: Annotated[str | None, CSS("h1::text")] = None


def test_html_model_without_required_field_rejected() -> None:
    """D-18: a document model with nothing required matches every page, error pages too."""

    class Service(SyncApi):
        @api.get("/catalogue")
        def get(self) -> BlockPage:
            raise NotImplementedError

    with (
        _mock_client(_echo(200, "text/html")) as client,
        pytest.raises(BackendCapabilityError) as failure,
    ):
        Service(client).get()
    assert "Html model BlockPage matches any document; add a required selector field or when=" in (
        str(failure.value)
    )


# --- 50.2.8 signature pointers name fields the body has -------------------------------

SIGNING_KEY = SigningKeyRequirement("phase50")


def test_signature_pointer_validated_against_wire_names() -> None:
    """D-19: a pointer into a flat JSON body must name one of its fields."""

    class Service(SyncApi):
        @api.post(
            "/orders",
            operation_id="CreateOrder",
            signing=hmac_sha256(
                key=SIGNING_KEY,
                base=canonical_json(include=("/Data",)),
                output=header_output("X-Sig"),
            ),
        )
        def create(
            self, *, data: JsonField[str], meta: JsonField[str] = "", token: JsonField[str] = ""
        ) -> None:
            raise NotImplementedError

    with pytest.raises(PlanError) as failure:
        cast(Any, Service.create).resolve().compile()
    assert str(failure.value) == (
        "signature pointer '/Data' is not a field of the request body of 'CreateOrder'; "
        "available: /data, /meta, /token"
    )


class Renaming(msgspec.Struct, frozen=True, kw_only=True):
    """The model owns the wire name: ``payload`` is sent as ``data``."""

    payload: str = msgspec.field(name="data")


def test_signature_pointer_uses_model_rename() -> None:
    """The names it checks are the wire names, not the Python ones the model renamed."""

    class Service(SyncApi):
        @api.post(
            "/orders",
            operation_id="CreateOrder",
            signing=hmac_sha256(
                key=SIGNING_KEY,
                base=canonical_json(include=("/data",)),
                output=header_output("X-Sig"),
            ),
        )
        def create(self, *, body: Annotated[Renaming, markers.JsonBody()]) -> None:
            raise NotImplementedError

        @api.post(
            "/orders",
            operation_id="CreateOrderPython",
            signing=hmac_sha256(
                key=SIGNING_KEY,
                base=canonical_json(include=("/payload",)),
                output=header_output("X-Sig"),
            ),
        )
        def create_by_python_name(self, *, body: Annotated[Renaming, markers.JsonBody()]) -> None:
            raise NotImplementedError

    cast(Any, Service.create).resolve().compile()
    with pytest.raises(PlanError) as failure:
        cast(Any, Service.create_by_python_name).resolve().compile()
    assert str(failure.value) == (
        "signature pointer '/payload' is not a field of the request body of "
        "'CreateOrderPython'; available: /data"
    )
