"""Treat a request as a value: build it once, change one field, send it again.

Paging is the example everyone has: the same call, one number apart. With the request as a
value there is no paging abstraction to learn — ``request()`` builds it, ``evolve()`` copies
it with the next page, ``send()`` sends it, and a queue of pending requests is a plain list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import Http, HttpOperation, Query, SyncApi, op
from eazy_sdk.request import markers
from examples.docs.request_site import request_client

@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[str, ...]
    next_page: int | None


# docs:operation:start
# examples/request_values.py
@dataclass(frozen=True, slots=True, kw_only=True)
class ListItems(HttpOperation[Page]):
    """Every field of the request is a field of this class, defaults included."""

    __http__ = Http.get("/items", operation_id="listItems")

    page: Query[int] = 1
    per_page: Annotated[int, markers.Query("perPage")] = 2


class CatalogApi(SyncApi):
    list_items = op(ListItems)
# docs:operation:end


def main() -> None:
    with request_client() as client:
        catalog = CatalogApi(client)

        # docs:flow:start
        # examples/request_values.py
        # One value, evolved per page: no Pages helper, no cursor object, no callback.
        request = catalog.list_items.request(per_page=2)
        collected: list[str] = []
        while True:
            page = catalog.list_items.send(request)
            collected.extend(page.items)
            if page.next_page is None:
                break
            request = catalog.list_items.evolve(request, page=page.next_page)
        print(f"collected: {', '.join(collected)}")

        # A queue of requests is a list of values; nothing is sent until it is.
        pending = [catalog.list_items.request(page=number, per_page=2) for number in (3, 1)]
        for item in pending:
            print(f"queued page: {catalog.list_items.send(item).items[0]}")
        # docs:flow:end


if __name__ == "__main__":
    main()
