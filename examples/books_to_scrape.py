"""Extract a typed catalog page from the public Books to Scrape sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, TypedDict, Unpack
from urllib.parse import urljoin

from eazy_sdk import CSS, Client, ClientConfig, Scope, SyncApi, api
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import Path
from eazy_sdk.response import Html, Responses, Success

BASE_URL = "https://books.toscrape.com"


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


CATALOG_RESPONSE: Responses[CatalogPage] = Responses(
    success=(Success(200, Html(CatalogPage)),)
)


class GetCatalogPageRequest(TypedDict):
    page: Annotated[int, Path()]


class BooksApi(SyncApi):
    @api.get(
        "/catalogue/page-{page}.html",
        operation_id="getCatalogPage",
        responses=CATALOG_RESPONSE,
    )
    def page(self, **request: Unpack[GetCatalogPageRequest]) -> CatalogPage:
        raise NotImplementedError


def main() -> None:
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(),
        config=ClientConfig(timeout=20),
    ) as client:
        catalog = BooksApi(client).page(page=1)

    print(f"{catalog.title}: {len(catalog.books)} books")
    for book in catalog.books[:3]:
        print(f"- {book.title} | GBP {book.price_gbp} | rating {book.rating}")
    print(f"next: {catalog.next_href}")


if __name__ == "__main__":
    main()
