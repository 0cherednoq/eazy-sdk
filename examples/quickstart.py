"""Smallest deterministic Eazy SDK example, with no public network dependency."""

from __future__ import annotations

from typing import Annotated, TypedDict, Unpack

import httpx
from pydantic import BaseModel

from eazy_sdk import Client, SyncApi, api
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import Path
from eazy_sdk.response import Json, Responses, Success


class Product(BaseModel):
    id: int
    title: str
    price: int


PRODUCT_RESPONSES = Responses[Product](
    success=(Success(200, Json(Product)),)
)


class GetProductRequest(TypedDict):
    product_id: Annotated[int, Path()]


class StoreApi(SyncApi):
    @api.get("/v1/products/{product_id}", responses=PRODUCT_RESPONSES)
    def product(self, **request: Unpack[GetProductRequest]) -> Product:
        raise NotImplementedError


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
