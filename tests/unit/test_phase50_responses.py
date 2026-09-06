"""Phase 50.2: the response family comes from the model, never from the declaration site.

The four shapes measured in ``experiments/response_case_sugar/j_html_service.py`` are the
whole question: an HTML page, an HTML page nested inside another, an HTML error page on a
JSON service, and a JSON problem on an HTML service. None of them says ``Html(...)`` or
``Json(...)`` at the operation — the fields of the model already say which family reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, cast

import httpx
import pytest
from eazy_sdk_html import CSS, Scope
from pydantic import BaseModel

from eazy_sdk import Client, Http, HttpOperation, Path, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.models import default_model_adapters
from eazy_sdk.models.documents import is_document_model
from eazy_sdk.response import ApiError, Html, Json, Responses
from eazy_sdk.response._mapping import representation

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
