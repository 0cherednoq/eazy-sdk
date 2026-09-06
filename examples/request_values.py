"""Treat a request as a value: build it once, change one field, send it again.

Paging is the example everyone has: the same call, one number apart. With the request as a
value there is no paging abstraction to learn — ``request()`` builds it, ``evolve()`` copies
it with the next page, ``send()`` sends it, and a queue of pending requests is a plain list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx

from eazy_sdk import Client, Http, HttpOperation, Query, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import markers

BASE_URL = "https://api.catalog.example"


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[str, ...]
    next_page: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ListItems(HttpOperation[Page]):
    """Every field of the request is a field of this class, defaults included."""

    __http__ = Http.get("/items", operation_id="listItems")

    page: Query[int] = 1
    per_page: Annotated[int, markers.Query("perPage")] = 2


class CatalogApi(SyncApi):
    list_items = op(ListItems)


PAGES = {
    1: {"items": ["keyboard", "mouse"], "next_page": 2},
    2: {"items": ["monitor", "cable"], "next_page": 3},
    3: {"items": ["lamp"], "next_page": None},
}


def catalog_server(request: httpx.Request) -> httpx.Response:
    page = int(request.url.params.get("page", "1"))
    return httpx.Response(200, json=PAGES[page])


def main() -> None:
    raw_client = httpx.Client(transport=httpx.MockTransport(catalog_server))
    with Client(
        base_url=BASE_URL,
        handler=HttpxHandler(raw_client, owns_client=True),
    ) as client:
        catalog = CatalogApi(client)

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


if __name__ == "__main__":
    main()
