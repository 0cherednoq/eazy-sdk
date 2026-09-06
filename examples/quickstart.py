"""Smallest deterministic Eazy SDK example, with no public network dependency."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from eazy_sdk import Client, Http, HttpOperation, Path, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler


class Product(BaseModel):
    id: int
    title: str
    price: int


@dataclass(frozen=True, slots=True, kw_only=True)
class GetProduct(HttpOperation[Product]):
    """The request as a value: its fields are the inputs, ``__http__`` is the contract."""

    __http__ = Http.get("/v1/products/{product_id}")

    product_id: Path[int]


class StoreApi(SyncApi):
    product = op(GetProduct)


def store(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/products/42"
    return httpx.Response(
        200,
        json={"id": 42, "title": "Mechanical keyboard", "price": 12_900},
    )


def main() -> None:
    transport = httpx.MockTransport(store)
    raw_client = httpx.Client(transport=transport, headers={}, cookies={})

    with Client(
        base_url="https://api.store.example",
        handler=HttpxHandler(raw_client, owns_client=True),
    ) as client:
        product = StoreApi(client).product(product_id=42)

    print(product.title, product.price)


if __name__ == "__main__":
    main()
