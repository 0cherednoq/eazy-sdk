"""Extract a typed model from an HTML response."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated

from eazy_sdk_html import CSS, Scope

from eazy_sdk import Http, HttpOperation, Path, SyncApi, op
from examples.docs.request_site import request_client


# docs:models:start
# examples/docs/response_html.py
@dataclass(frozen=True, slots=True)
class Book:
    title: Annotated[str, CSS("a::text")]
    price_text: Annotated[str, CSS("span::text")]

    @property
    def price(self) -> Decimal:
        return Decimal(self.price_text)


@dataclass(frozen=True, slots=True)
class Catalogue:
    title: Annotated[str, CSS("h1::text")]
    books: Annotated[list[Book], Scope(CSS("article.book"))]
    next_href: Annotated[str | None, CSS("a.next::attr(href)")] = None
# docs:models:end


# docs:operation:start
# examples/docs/response_html.py
@dataclass(frozen=True, slots=True, kw_only=True)
class GetCatalogue(HttpOperation[Catalogue]):
    __http__ = Http.get("/catalogue/page-{page}.html")

    page: Path[int]


class CatalogueApi(SyncApi):
    page = op(GetCatalogue)
# docs:operation:end


def main() -> None:
    with request_client() as client:
        catalogue = CatalogueApi(client).page(page=1)

    print(f"{catalogue.title}: {len(catalogue.books)} books")
    print(f"first: {catalogue.books[0].title} {catalogue.books[0].price}")
    print(f"next: {catalogue.next_href}")


if __name__ == "__main__":
    main()
