"""Extract a typed catalog page from a local teaching endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated
from urllib.parse import urljoin

import httpx
from eazy_sdk_html import CSS, Scope

from eazy_sdk import Client, ClientConfig, Http, HttpOperation, Path, Resilience, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler

BASE_URL = "https://catalog.example"


@dataclass(frozen=True, slots=True)
class BookCard:
    title: Annotated[str, CSS("h3 a::attr(title)")]
    price_text: Annotated[str, CSS(".price_color::text")]
    href: Annotated[str, CSS("h3 a::attr(href)")]
    rating_class: Annotated[str, CSS("p.star-rating::attr(class)")]

    @property
    def price_gbp(self) -> Decimal:
        return Decimal(self.price_text.removeprefix("£"))

    @property
    def rating(self) -> str:
        return self.rating_class.rsplit(maxsplit=1)[-1]

    @property
    def absolute_url(self) -> str:
        return urljoin(f"{BASE_URL}/catalogue/", self.href)


@dataclass(frozen=True, slots=True)
class CatalogPage:
    title: Annotated[str, CSS("h1::text")]
    books: Annotated[list[BookCard], Scope(CSS("article.product_pod"))]
    next_href: Annotated[str | None, CSS("li.next a::attr(href)")] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GetCatalogPage(HttpOperation[CatalogPage]):
    """The model carries CSS selectors, so the response is read as a document, not JSON."""

    __http__ = Http.get("/catalogue/page-{page}.html", operation_id="getCatalogPage")

    page: Path[int]


class BooksApi(SyncApi):
    page = op(GetCatalogPage)


def _catalog_site(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content="""
        <html><body><h1>Books</h1>
          <article class="product_pod"><p class="star-rating Five"></p>
            <h3><a href="one/index.html" title="Typed clients">One</a></h3>
            <p class="price_color">£12.50</p></article>
          <article class="product_pod"><p class="star-rating Four"></p>
            <h3><a href="two/index.html" title="Request pipelines">Two</a></h3>
            <p class="price_color">£18.00</p></article>
          <article class="product_pod"><p class="star-rating Three"></p>
            <h3><a href="three/index.html" title="Response models">Three</a></h3>
            <p class="price_color">£9.75</p></article>
          <li class="next"><a href="page-2.html">next</a></li>
        </body></html>
        """.encode(),
    )


def main() -> None:
    raw = httpx.Client(
        transport=httpx.MockTransport(_catalog_site),
        headers={},
        cookies={},
    )
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(resilience=Resilience(timeout=20)),
    ) as client:
        catalog = BooksApi(client).page(page=1)

    print(f"{catalog.title}: {len(catalog.books)} books")
    for book in catalog.books[:3]:
        print(f"- {book.title} | GBP {book.price_gbp} | rating {book.rating}")
    print(f"next: {catalog.next_href}")


if __name__ == "__main__":
    main()
