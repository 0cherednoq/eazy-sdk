"""Map documented HTTP responses to values and typed API errors."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from eazy_sdk import Client, Http, HttpOperation, Path, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.response import ApiError


class Order(BaseModel):
    id: str
    status: str
    total: int


class ApiProblem(BaseModel):
    code: str
    message: str


class OrderNotFound(ApiError[ApiProblem]):
    pass


class RateLimited(ApiError[ApiProblem]):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class GetOrder(HttpOperation[Order]):
    """``HttpOperation[Order]`` is the 200 case; each error names its own exception class."""

    __http__ = Http.get(
        "/orders/{order_id}",
        operation_id="getOrder",
        errors={404: OrderNotFound, 429: RateLimited},
    )

    order_id: Path[str]


class OrdersApi(SyncApi):
    get_order = op(GetOrder)


def order_server(request: httpx.Request) -> httpx.Response:
    order_id = request.url.path.rsplit("/", maxsplit=1)[-1]
    if order_id == "order-42":
        return httpx.Response(
            200,
            json={"id": order_id, "status": "paid", "total": 12_900},
        )
    if order_id == "busy":
        return httpx.Response(
            429,
            json={"code": "rate_limited", "message": "Try again later"},
        )
    return httpx.Response(
        404,
        json={"code": "order_not_found", "message": f"Unknown order {order_id}"},
    )


def main() -> None:
    raw_client = httpx.Client(
        transport=httpx.MockTransport(order_server),
        headers={},
        cookies={},
    )
    with Client(
        base_url="https://api.store.example",
        handler=HttpxHandler(raw_client, owns_client=True),
    ) as client:
        orders = OrdersApi(client)
        order = orders.get_order.with_response(order_id="order-42")
        print(f"{order.status_code}: {order.value.id} is {order.value.status}")

        try:
            orders.get_order(order_id="missing")
        except OrderNotFound as error:
            print(f"{error.context.response.status_code}: {error.error.message}")

        try:
            orders.get_order(order_id="busy")
        except RateLimited as error:
            print(f"{error.context.response.status_code}: {error.error.code}")


if __name__ == "__main__":
    main()
