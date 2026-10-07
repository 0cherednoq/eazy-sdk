"""Read a typed value together with HTTP response metadata."""

from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk import Http, HttpOperation, Path, SyncApi, op
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    status: str
    total: int


# docs:example:start
# examples/docs/response_success.py
@dataclass(frozen=True, slots=True, kw_only=True)
class GetOrder(HttpOperation[Order]):
    __http__ = Http.get("/orders/{order_id}")

    order_id: Path[str]


class OrdersApi(SyncApi):
    get = op(GetOrder)
# docs:example:end


def main() -> None:
    with request_client() as client:
        response = OrdersApi(client).get.with_response(order_id="order-42")

    print(f"status: {response.status_code}")
    print(f"order: {response.value.id} {response.value.status}")
    print(f"request id: {response.headers['x-request-id']}")


if __name__ == "__main__":
    main()
