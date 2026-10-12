# Pagination

The page loop belongs to the library. Declare the strategy on the operation with `__pages__`;
routers get no generator methods.

Declare it only when the inventory gives evidence: a `pagination hint` on the operation, and
ideally two samples with different page parameters. With one sample, say in the plan that
the strategy is inferred from parameter names.

```python
from dataclasses import dataclass

from eazy_sdk import Http, HttpOperation, Query
from eazy_sdk.pagination import Pages


@dataclass(frozen=True, slots=True, kw_only=True)
class ListThreads(HttpOperation[ThreadsPage]):
    __http__ = Http.get("/api/v1/threads")
    __pages__ = Pages.offset(
        ThreadsPage,
        offset="offset",
        limit="limit",
        items=lambda page: page.threads,
        total=lambda page: page.threads_total,
    )

    offset: Query[int] = 0
    limit: Query[int] = 50
```

The result class is the first argument, so the lambdas are typed. `offset=`, `limit=`,
`page=`, `size=`, `cursor=` are the Python names of the operation's fields.

| The inventory shows | Strategy |
|---|---|
| `offset` and `limit`, a total in the response | `Pages.offset(Model, offset=, limit=, items=, total=)` |
| `page` and a page size, a page count | `Pages.numbered(Model, page=, size=, items=, total_pages=)` |
| a token from the previous response | `Pages.cursor(Model, cursor=, items=, next_cursor=)` — stops on `None` |
| a link to the next page in the response | `Pages.next_url(Model, items=, next_url=)` |

`total=` and `total_pages=` are optional: without them the loop stops on an empty page or a
page shorter than the size.

What the caller gets, with the plain call unchanged:

```python
page = await sdk.mail.list(folder=0)                      # one page
async for page in sdk.mail.list.pages(folder=0): ...      # pages
async for item in sdk.mail.list.items(folder=0): ...      # elements
first = await sdk.mail.list.first(folder=0)               # first element or None
```

`items(key=lambda item: item.id)` drops repeats, `max_items=` and `max_pages=` bound the walk.

## In the replay

A scenario may call `mail.list.items`; the result is collected into a list, so `returns =
"list"` and `lengths = { "." = 40 }` describe it. The walk makes one request per recorded
page, in order: list every page's exchange in the scenario.
