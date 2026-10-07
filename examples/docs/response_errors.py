"""Map expected error responses to typed exceptions."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Path, SyncApi, op
from eazy_sdk.response import ApiError
from examples.docs.request_site import request_client


class ApiProblem(BaseModel):
    code: str
    message: str


class OrderNotFound(ApiError[ApiProblem]):
    pass


class RateLimited(ApiError[ApiProblem]):
    pass


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    status: str
    total: int


# docs:example:start
# examples/docs/response_errors.py
@dataclass(frozen=True, slots=True, kw_only=True)
class GetOrder(HttpOperation[Order]):
    __http__ = Http.get(
        "/orders/{order_id}",
        errors={404: OrderNotFound, 429: RateLimited},
    )

    order_id: Path[str]


class OrdersApi(SyncApi):
    get = op(GetOrder)
# docs:example:end


def main() -> None:
    with request_client() as client:
        orders = OrdersApi(client)
        for order_id in ("missing", "busy"):
            try:
                orders.get(order_id=order_id)
            except OrderNotFound as error:
                print(f"{error.summary.status_code}: {error.error.message}")
            except RateLimited as error:
                print(f"{error.summary.status_code}: {error.error.code}")


if __name__ == "__main__":
    main()
