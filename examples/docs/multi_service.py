"""Compose two service routers into one SDK root."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import (
    Http,
    HttpOperation,
    JsonField,
    Path,
    SyncApi,
    SyncRoot,
    api_group,
    bind,
    op,
)
from examples.docs.guide_site import GuideSite, guide_client


class Result(BaseModel):
    value: str


# region docs: multi-service
# examples/docs/multi_service.py
class BillingService:
    base_url = "https://billing.mail.example"


@dataclass(frozen=True, slots=True, kw_only=True)
class GetBook(HttpOperation[Result]):
    __http__ = Http.get("/books/{book_id}")
    book_id: Path[int]


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateCard(HttpOperation[Result]):
    __http__ = Http.post("/cards", success={201: Result})
    label: JsonField[str]


class BooksApi(SyncApi):
    get = op(GetBook)


class CardsApi(BillingService, SyncApi):
    create = op(CreateCard)


class ShopSdk(SyncRoot):
    books = api_group(BooksApi)
    cards = api_group(CardsApi)


def main() -> None:
    site = GuideSite()
    with guide_client(site) as client:
        sdk = ShopSdk(
            client,
            bindings=(
                bind(BillingService, base_url="https://stage-billing.mail.example"),
            ),
        )
        book = sdk.books.get(book_id=7)
        card = sdk.cards.create(label="main")
    print(f"book: {book.value}")
    print(f"card: {card.value}")
    print(*site.calls, sep="\n")
# endregion docs: multi-service


if __name__ == "__main__":
    main()
