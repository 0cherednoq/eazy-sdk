"""Phase 52: pagination is declared on the operation class and iterated on the router.

The strategy is data (``next_changes`` is tested without a client); every page goes through the
ordinary ``send()`` of the bound operation; declaration errors surface when ``op()`` runs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx
import pytest

from eazy_sdk import UNSET, AsyncApi, Http, HttpOperation, Omittable, Query, SyncApi, op
from eazy_sdk.core.errors import PlanError
from eazy_sdk.middleware import call_middleware
from eazy_sdk.pagination import NumberedPages, Pages, next_changes
from eazy_sdk.policies import CallOptions
from tests._support.zapros_clients import client_from_httpx

# --- the operation under test ---------------------------------------------------------------


@dataclass(frozen=True)
class Document:
    id: str


@dataclass(frozen=True)
class DocumentsPage:
    items: list[Document]
    pages_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ListDocuments(HttpOperation[DocumentsPage]):
    __http__ = Http.get("/documents")
    __pages__ = Pages.numbered(
        DocumentsPage,
        page="page",
        size="per_page",
        items=lambda r: r.items,
        total_pages=lambda r: r.pages_count,
    )

    case_id: Query[str]
    page: Query[int] = 1
    per_page: Query[int] = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class ListPlain(HttpOperation[DocumentsPage]):
    """No ``size`` and no ``total_pages``: only the empty-page rule stops it."""

    __http__ = Http.get("/documents")
    __pages__ = Pages.numbered(DocumentsPage, page="page", items=lambda r: r.items)

    page: Query[int] = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class GetOne(HttpOperation[DocumentsPage]):
    __http__ = Http.get("/documents")

    page: Query[int] = 1


class Api(SyncApi):
    documents = op(ListDocuments)
    plain = op(ListPlain)
    one = op(GetOne)


class AsyncDocsApi(AsyncApi):
    documents = op(ListDocuments)
    plain = op(ListPlain)


def _server(
    pages: Sequence[Sequence[str]], *, repeat_last: bool = False, seen: list[httpx.Request]
) -> Callable[[httpx.Request], httpx.Response]:
    """Serve ``pages`` by number; past the end, an empty page or the last one again."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        number = int(request.url.params.get("page", "1"))
        if number <= len(pages):
            ids = pages[number - 1]
        elif repeat_last:
            ids = pages[-1]
        else:
            ids = ()
        return httpx.Response(
            200, json={"items": [{"id": i} for i in ids], "pages_count": len(pages)}
        )

    return handler


def _sync(
    pages: Sequence[Sequence[str]], *, repeat_last: bool = False
) -> tuple[Api, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    client = client_from_httpx(
        httpx.Client(
            base_url="https://api.example",
            transport=httpx.MockTransport(_server(pages, repeat_last=repeat_last, seen=seen)),
        )
    )
    return Api(client), seen


def _async(
    pages: Sequence[Sequence[str]], *, repeat_last: bool = False
) -> tuple[AsyncDocsApi, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(_server(pages, repeat_last=repeat_last, seen=seen)),
        )
    )
    return AsyncDocsApi(client), seen


def _page_numbers(seen: list[httpx.Request]) -> list[int]:
    return [int(request.url.params.get("page", "1")) for request in seen]


# --- the strategy, without I/O (plan §3.4) --------------------------------------------------


def _page(ids: Sequence[str], pages_count: int = 9) -> DocumentsPage:
    return DocumentsPage(items=[Document(i) for i in ids], pages_count=pages_count)


def test_numbered_stops_on_empty() -> None:
    request = ListDocuments(case_id="c", page=1, per_page=2)
    assert next_changes(ListDocuments.__pages__, request, _page(()), fresh=0) is None


def test_numbered_stops_below_size() -> None:
    request = ListDocuments(case_id="c", page=1, per_page=2)
    assert next_changes(ListDocuments.__pages__, request, _page(("a",)), fresh=1) is None


def test_numbered_stops_at_total_pages() -> None:
    request = ListDocuments(case_id="c", page=3, per_page=2)
    page = _page(("a", "b"), pages_count=3)
    assert next_changes(ListDocuments.__pages__, request, page, fresh=2) is None


def test_numbered_advances() -> None:
    request = ListDocuments(case_id="c", page=3, per_page=2)
    page = _page(("a", "b"), pages_count=9)
    assert next_changes(ListDocuments.__pages__, request, page, fresh=2) == {"page": 4}


def test_numbered_rules_order() -> None:
    """A full page with no fresh element stops before ``total_pages`` is even consulted."""

    calls: list[str] = []

    def counting_total(result: DocumentsPage) -> int:
        calls.append("total")
        return 99

    strategy = Pages.numbered(
        DocumentsPage,
        page="page",
        size="per_page",
        items=lambda r: r.items,
        total_pages=counting_total,
    )
    request = ListDocuments(case_id="c", page=1, per_page=2)
    assert next_changes(strategy, request, _page(("a", "b")), fresh=0) is None
    assert calls == []
    # Rule 2 (total) stops before rule 3 (size) would have let a full page through.
    assert next_changes(strategy, request, _page(("a", "b")), fresh=2) == {"page": 2}
    assert calls == ["total"]


def test_numbered_requires_int_page() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Loose(HttpOperation[DocumentsPage]):
        __http__ = Http.get("/documents")
        __pages__ = Pages.numbered(DocumentsPage, page="page", items=lambda r: r.items)

        page: Query[Omittable[int]] = UNSET

    with pytest.raises(PlanError, match=r"Loose\.page must be an int to paginate, got Unset"):
        next_changes(Loose.__pages__, Loose(), _page(("a",)), fresh=1)


def test_numbered_size_is_ignored_when_not_int() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Loose(HttpOperation[DocumentsPage]):
        __http__ = Http.get("/documents")
        __pages__ = Pages.numbered(
            DocumentsPage, page="page", size="per_page", items=lambda r: r.items
        )

        page: Query[int] = 1
        per_page: Query[Omittable[int]] = UNSET

    assert next_changes(Loose.__pages__, Loose(), _page(("a",)), fresh=1) == {"page": 2}


# --- iteration through the router ------------------------------------------------------------


def test_pages_yields_every_page_and_stops() -> None:
    api, seen = _sync([("a", "b"), ("c", "d"), ("e",)])
    pages = list(api.documents.pages(case_id="c"))
    assert [[d.id for d in page.items] for page in pages] == [["a", "b"], ["c", "d"], ["e"]]
    assert _page_numbers(seen) == [1, 2, 3]
    assert seen[0].url.params["case_id"] == "c"


def test_items_flattens_pages() -> None:
    api, _ = _sync([("a", "b"), ("c", "d"), ("e",)])
    assert [d.id for d in api.documents.items(case_id="c")] == ["a", "b", "c", "d", "e"]


def test_items_key_dedupes_and_stops_on_repeated_page() -> None:
    """The server answers the last page again past the end; ``plain`` has no size or total."""

    api, seen = _sync([("a", "b"), ("b", "c")], repeat_last=True)
    got = [d.id for d in api.plain.items(key=lambda d: d.id)]
    assert got == ["a", "b", "c"]
    # Page 3 repeats page 2, contributes nothing new, and ends the loop.
    assert _page_numbers(seen) == [1, 2, 3]


def test_items_without_key_stops_on_empty_page_only() -> None:
    api, seen = _sync([("a", "b"), ("b", "c")])
    assert [d.id for d in api.plain.items()] == ["a", "b", "b", "c"]
    assert _page_numbers(seen) == [1, 2, 3]


def test_max_pages_bounds_iteration() -> None:
    api, seen = _sync([("a", "b"), ("c", "d"), ("e", "f")])
    assert len(list(api.documents.pages(case_id="c", max_pages=2))) == 2
    assert _page_numbers(seen) == [1, 2]
    seen.clear()
    assert [d.id for d in api.documents.items(case_id="c", max_pages=1)] == ["a", "b"]
    assert _page_numbers(seen) == [1]


def test_first_page_is_the_request_value() -> None:
    api, seen = _sync([("a", "b"), ("c", "d"), ("e", "f")])
    pages = list(api.documents.pages(case_id="c", page=3))
    assert [[d.id for d in page.items] for page in pages] == [["e", "f"]]
    assert _page_numbers(seen) == [3]


def test_short_page_stops_before_total() -> None:
    """Two of three pages, the second shorter than ``per_page``: rule 3 ends the loop."""

    api, seen = _sync([("a", "b"), ("c",), ("d", "e")])
    assert [d.id for d in api.documents.items(case_id="c")] == ["a", "b", "c"]
    assert _page_numbers(seen) == [1, 2]


def test_options_reach_every_page() -> None:
    """``options=`` is handed to every ``send()``: a per-call middleware sees each page."""

    calls: list[str] = []

    async def counting(context: Any, call_next: Any) -> Any:
        calls.append(context.operation.operation_id)
        return await call_next(context)

    api, seen = _sync([("a", "b"), ("c", "d")])
    options = CallOptions(middleware=(call_middleware(counting),))
    list(api.documents.pages(case_id="c", options=options))
    assert _page_numbers(seen) == [1, 2]
    assert len(calls) == 2


async def test_async_router_returns_async_generators() -> None:
    api, seen = _async([("a", "b"), ("c", "d"), ("e",)])
    pages = [page async for page in api.documents.pages(case_id="c")]
    assert [[d.id for d in page.items] for page in pages] == [["a", "b"], ["c", "d"], ["e"]]
    seen.clear()
    items = [d.id async for d in api.plain.items(max_pages=2)]
    assert items == ["a", "b", "c", "d"]
    assert _page_numbers(seen) == [1, 2]


async def test_async_items_key_dedupes_and_stops() -> None:
    api, seen = _async([("a", "b"), ("b", "c")], repeat_last=True)
    assert [d.id async for d in api.plain.items(key=lambda d: d.id)] == ["a", "b", "c"]
    assert _page_numbers(seen) == [1, 2, 3]


def test_ordinary_call_is_unchanged() -> None:
    """I1: ``__pages__`` changes nothing about ``api.op(...)``."""

    api, seen = _sync([("a", "b"), ("c", "d")])
    result = api.documents(case_id="c", page=2)
    assert [d.id for d in result.items] == ["c", "d"]
    assert _page_numbers(seen) == [2]
    assert json.loads(json.dumps(dict(seen[0].url.params))) == {
        "case_id": "c",
        "page": "2",
        "per_page": "2",
    }


# --- declaration errors, when ``op()`` runs -----------------------------------------------------


def test_pages_rejects_unknown_field() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Typo(HttpOperation[DocumentsPage]):
        __http__ = Http.get("/documents")
        __pages__ = Pages.numbered(DocumentsPage, page="pgae", items=lambda r: r.items)

        page: Query[int] = 1

    with pytest.raises(
        PlanError, match=r"Typo\.__pages__ names unknown field 'pgae'; fields: page"
    ):
        op(Typo)


def test_pages_rejects_wrong_result_type() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Mismatch(HttpOperation[DocumentsPage]):
        __http__ = Http.get("/documents")
        __pages__ = Pages.numbered(Document, page="page", items=lambda r: [r])

        page: Query[int] = 1

    with pytest.raises(
        PlanError,
        match=r"Mismatch\.__pages__ declares result Document, the operation returns DocumentsPage",
    ):
        op(Mismatch)


def test_pages_follows_success_when_no_generic() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Explicit(HttpOperation[Any]):
        __http__ = Http.get("/documents", success={200: DocumentsPage})
        __pages__ = Pages.numbered(DocumentsPage, page="page", items=lambda r: r.items)

        page: Query[int] = 1

    # ``HttpOperation[Any]`` says nothing (phase 50), so the result is what ``success=`` names.
    assert op(Explicit).pages is Explicit.__pages__


def test_pages_rejects_non_strategy() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Wrong(HttpOperation[DocumentsPage]):
        __http__ = Http.get("/documents")
        __pages__: ClassVar[Any] = {"page": "page"}

        page: Query[int] = 1

    with pytest.raises(
        PlanError, match=r"operation class Wrong\.__pages__ must be a Pages strategy, got dict"
    ):
        op(Wrong)


def test_pages_without_declaration() -> None:
    api, _ = _sync([])
    with pytest.raises(PlanError, match="pages\\(\\) requires __pages__ on GetOne"):
        next(api.one.pages())
    with pytest.raises(PlanError, match="pages\\(\\) requires __pages__ on GetOne"):
        next(api.one.items())


def test_max_pages_validation() -> None:
    api, _ = _sync([("a",)])
    for bad in (0, -1, True):
        with pytest.raises(ValueError, match="max_pages must be None or >= 1"):
            next(api.documents.pages(case_id="c", max_pages=bad))


def test_strategy_constructor_validation() -> None:
    with pytest.raises(TypeError, match="result class as its first argument"):
        Pages.numbered("DocumentsPage", page="page", items=lambda r: [])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="page=\\) must be a field name"):
        Pages.numbered(DocumentsPage, page="", items=lambda r: r.items)
    with pytest.raises(TypeError, match="items=\\) must be callable"):
        Pages.numbered(DocumentsPage, page="page", items=None)  # type: ignore[arg-type]
    strategy = Pages.numbered(DocumentsPage, page="page", size="per_page", items=lambda r: r.items)
    assert isinstance(strategy, NumberedPages)
    assert strategy.fields == ("page", "per_page")


def test_root_export_budget_is_unchanged() -> None:
    import eazy_sdk

    assert "Pages" not in eazy_sdk.__all__
    assert len(eazy_sdk.__all__) <= 40


# --- 52.2: Pages.offset ---------------------------------------------------------------------


@dataclass(frozen=True)
class Slice:
    items: list[Document]
    total: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ListOffset(HttpOperation[Slice]):
    __http__ = Http.get("/offset")
    __pages__ = Pages.offset(
        Slice, offset="offset", limit="limit", items=lambda r: r.items, total=lambda r: r.total
    )

    offset: Query[int] = 0
    limit: Query[int] = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class ListOffsetPlain(HttpOperation[Slice]):
    """No ``limit`` and no ``total``: only an empty page stops it."""

    __http__ = Http.get("/offset")
    __pages__ = Pages.offset(Slice, offset="offset", items=lambda r: r.items)

    offset: Query[int] = 0


class OffsetApi(SyncApi):
    sliced = op(ListOffset)
    plain = op(ListOffsetPlain)


def _offset_api(ids: Sequence[str], *, short_by: int = 0) -> tuple[OffsetApi, list[httpx.Request]]:
    """Serve ``ids`` by offset; ``short_by`` makes every page that many elements shorter."""

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        offset = int(request.url.params.get("offset", "0"))
        limit = int(request.url.params.get("limit", "2")) - short_by
        chunk = ids[offset : offset + limit]
        return httpx.Response(200, json={"items": [{"id": i} for i in chunk], "total": len(ids)})

    client = client_from_httpx(
        httpx.Client(base_url="https://api.example", transport=httpx.MockTransport(handler))
    )
    return OffsetApi(client), seen


def _offsets(seen: list[httpx.Request]) -> list[int]:
    return [int(request.url.params.get("offset", "0")) for request in seen]


def _slice(ids: Sequence[str], total: int = 99) -> Slice:
    return Slice(items=[Document(i) for i in ids], total=total)


def test_offset_stops_on_empty() -> None:
    assert next_changes(ListOffset.__pages__, ListOffset(), _slice(()), fresh=0) is None


def test_offset_stops_at_total() -> None:
    request = ListOffset(offset=4, limit=2)
    assert next_changes(ListOffset.__pages__, request, _slice(("e", "f"), total=6), fresh=2) is None


def test_offset_stops_below_limit() -> None:
    request = ListOffset(offset=0, limit=2)
    assert next_changes(ListOffset.__pages__, request, _slice(("a",)), fresh=1) is None


def test_offset_advances_by_returned_count() -> None:
    """A server that answers fewer than ``limit`` without ``limit`` declared is followed exactly."""

    request = ListOffsetPlain(offset=3)
    assert next_changes(ListOffsetPlain.__pages__, request, _slice(("a",)), fresh=1) == {
        "offset": 4
    }
    limited = ListOffset(offset=2, limit=2)
    assert next_changes(ListOffset.__pages__, limited, _slice(("c", "d")), fresh=2) == {"offset": 4}


def test_offset_rules_order() -> None:
    calls: list[str] = []

    def counting_total(result: Slice) -> int:
        calls.append("total")
        return 99

    strategy = Pages.offset(
        Slice, offset="offset", limit="limit", items=lambda r: r.items, total=counting_total
    )
    request = ListOffset(offset=0, limit=2)
    assert next_changes(strategy, request, _slice(("a", "b")), fresh=0) is None
    assert calls == []
    assert next_changes(strategy, request, _slice(("a", "b")), fresh=2) == {"offset": 2}
    assert calls == ["total"]


def test_offset_requires_int_offset() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Loose(HttpOperation[Slice]):
        __http__ = Http.get("/offset")
        __pages__ = Pages.offset(Slice, offset="offset", items=lambda r: r.items)

        offset: Query[Omittable[int]] = UNSET

    with pytest.raises(PlanError, match=r"Loose\.offset must be an int to paginate, got Unset"):
        next_changes(Loose.__pages__, Loose(), _slice(("a",)), fresh=1)


def test_offset_pages_and_items_through_the_router() -> None:
    api, seen = _offset_api(["a", "b", "c", "d", "e"])
    pages = list(api.sliced.pages())
    assert [[d.id for d in page.items] for page in pages] == [["a", "b"], ["c", "d"], ["e"]]
    assert _offsets(seen) == [0, 2, 4]
    seen.clear()
    assert [d.id for d in api.sliced.items(limit=3)] == ["a", "b", "c", "d", "e"]
    assert _offsets(seen) == [0, 3]


def test_offset_follows_a_server_that_returns_fewer_than_asked() -> None:
    """``plain`` has no limit or total: the next offset is what the server actually returned."""

    api, seen = _offset_api(["a", "b", "c", "d", "e"], short_by=1)
    assert [d.id for d in api.plain.items()] == ["a", "b", "c", "d", "e"]
    assert _offsets(seen) == [0, 1, 2, 3, 4, 5]


def test_offset_first_page_is_the_request_value() -> None:
    api, seen = _offset_api(["a", "b", "c", "d", "e"])
    assert [d.id for d in api.sliced.items(offset=3)] == ["d", "e"]
    assert _offsets(seen) == [3]


def test_offset_declaration_errors() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Typo(HttpOperation[Slice]):
        __http__ = Http.get("/offset")
        __pages__ = Pages.offset(Slice, offset="offest", items=lambda r: r.items)

        offset: Query[int] = 0

    with pytest.raises(PlanError, match=r"Typo\.__pages__ names unknown field 'offest'"):
        op(Typo)
    with pytest.raises(TypeError, match=r"offset=\) must be a field name"):
        Pages.offset(Slice, offset="", items=lambda r: r.items)
    assert Pages.offset(Slice, offset="offset", limit="limit", items=lambda r: r.items).fields == (
        "offset",
        "limit",
    )
