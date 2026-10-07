"""Declare the same operation as a class and with the compact decorator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import Http, HttpOperation, JsonField, SyncApi, api, op
from eazy_sdk.request import markers
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    item: str


# docs:class:start
# examples/docs/request_declaration.py
@dataclass(frozen=True, slots=True, kw_only=True)
class CreateOrder(HttpOperation[Order]):
    __http__ = Http.post("/orders", success={201: Order})

    item: JsonField[str]


class OrdersApi(SyncApi):
    create = op(CreateOrder)
# docs:class:end


# docs:decorator:start
# examples/docs/request_declaration.py
class CompactOrdersApi(SyncApi):
    @api.post("/orders", success={201: Order})
    def create(
        self,
        *,
        item: Annotated[str, markers.JsonField()],
    ) -> Order:
        raise NotImplementedError
# docs:decorator:end


def main() -> None:
    with request_client() as client:
        declared = OrdersApi(client).create(item="keyboard")
        compact = CompactOrdersApi(client).create(item="mouse")

    print(f"class: {declared.id} {declared.item}")
    print(f"decorator: {compact.id} {compact.item}")


if __name__ == "__main__":
    main()
