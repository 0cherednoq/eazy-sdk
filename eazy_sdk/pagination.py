"""Declarative pagination: a strategy on the operation class, iteration on the router.

::

    @dataclass(frozen=True, slots=True, kw_only=True)
    class ListOrders(HttpOperation[OrdersPage]):
        __http__ = Http.get("/orders")
        __pages__ = Pages.numbered(
            OrdersPage,
            page="page",
            size="per_page",
            items=lambda r: r.items,
            total_pages=lambda r: r.pages_count,
        )

        page: Query[int] = 1
        per_page: Query[int] = 25

    for order in api.list_orders.items(per_page=100):
        ...

A strategy is data without I/O. It answers one question — given the request that produced a
page, the page and how many of its elements were used, what does the next request look like,
or is the iteration over — and :func:`next_changes` is that answer. Every page is sent through
the ordinary bound operation (``request()`` → ``send()`` → ``evolve()`` → ``send()`` …), so
retries, middleware, protections and ``options=`` apply to each one.

The result type is the first argument of :meth:`Pages.numbered` so that the ``items=`` and
``total_pages=`` callables are typed: an IDE completes ``r.`` and mypy checks the attribute.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from eazy_sdk.core.errors import PlanError

__all__ = [
    "CursorPages",
    "NextUrl",
    "NextUrlPages",
    "NumberedPages",
    "OffsetPages",
    "Pages",
    "Pagination",
    "next_changes",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class NumberedPages[T]:
    """Pages addressed by a number in one request field.

    ``page`` and ``size`` are Python field names of the operation class, since the next request
    is built through the model's own ``evolve``. The first page is whatever the request value
    says; the strategy only ever adds one to it.
    """

    result: type[T]
    page: str
    items: Callable[[T], Sequence[object]]
    size: str | None = None
    total_pages: Callable[[T], int] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result, type):
            raise TypeError("Pages.numbered() expects the result class as its first argument")
        if not isinstance(self.page, str) or not self.page:
            raise TypeError("Pages.numbered(page=) must be a field name")
        if self.size is not None and (not isinstance(self.size, str) or not self.size):
            raise TypeError("Pages.numbered(size=) must be a field name or None")
        if not callable(self.items):
            raise TypeError("Pages.numbered(items=) must be callable")
        if self.total_pages is not None and not callable(self.total_pages):
            raise TypeError("Pages.numbered(total_pages=) must be callable or None")

    @property
    def fields(self) -> tuple[str, ...]:
        """The operation fields the strategy reads and rewrites."""

        return (self.page,) if self.size is None else (self.page, self.size)


@dataclass(frozen=True, slots=True, kw_only=True)
class OffsetPages[T]:
    """Pages addressed by the number of elements already seen, in one request field.

    ``offset`` and ``limit`` are Python field names of the operation class. The next offset is
    the current one plus the length of the page the server actually returned, so a server that
    answers fewer elements than asked is followed, not skipped over.
    """

    result: type[T]
    offset: str
    items: Callable[[T], Sequence[object]]
    limit: str | None = None
    total: Callable[[T], int] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result, type):
            raise TypeError("Pages.offset() expects the result class as its first argument")
        if not isinstance(self.offset, str) or not self.offset:
            raise TypeError("Pages.offset(offset=) must be a field name")
        if self.limit is not None and (not isinstance(self.limit, str) or not self.limit):
            raise TypeError("Pages.offset(limit=) must be a field name or None")
        if not callable(self.items):
            raise TypeError("Pages.offset(items=) must be callable")
        if self.total is not None and not callable(self.total):
            raise TypeError("Pages.offset(total=) must be callable or None")

    @property
    def fields(self) -> tuple[str, ...]:
        return (self.offset,) if self.limit is None else (self.offset, self.limit)


@dataclass(frozen=True, slots=True, kw_only=True)
class CursorPages[T]:
    """Pages addressed by an opaque token the previous page handed out.

    ``cursor`` is the Python field name that carries the token; the first page sends whatever
    the request value holds there (usually ``UNSET`` or ``None``). ``next_cursor`` reads the
    token for the following page out of the result; ``None`` means there is no following page.
    """

    result: type[T]
    cursor: str
    items: Callable[[T], Sequence[object]]
    next_cursor: Callable[[T], object | None]

    def __post_init__(self) -> None:
        if not isinstance(self.result, type):
            raise TypeError("Pages.cursor() expects the result class as its first argument")
        if not isinstance(self.cursor, str) or not self.cursor:
            raise TypeError("Pages.cursor(cursor=) must be a field name")
        if not callable(self.items):
            raise TypeError("Pages.cursor(items=) must be callable")
        if not callable(self.next_cursor):
            raise TypeError("Pages.cursor(next_cursor=) must be callable")

    @property
    def fields(self) -> tuple[str, ...]:
        return (self.cursor,)


@dataclass(frozen=True, slots=True, kw_only=True)
class NextUrlPages[T]:
    """Pages addressed by a link the previous page handed out.

    The link is sent as it is: the operation's path and query fields are not re-applied to it,
    since the server already encoded whatever it needs there. Headers, cookies and body fields
    still come from the request value. A relative link is resolved against the URL of the page
    that returned it; ``None`` from ``next_url`` means there is no following page.
    """

    result: type[T]
    items: Callable[[T], Sequence[object]]
    next_url: Callable[[T], str | None]

    def __post_init__(self) -> None:
        if not isinstance(self.result, type):
            raise TypeError("Pages.next_url() expects the result class as its first argument")
        if not callable(self.items):
            raise TypeError("Pages.next_url(items=) must be callable")
        if not callable(self.next_url):
            raise TypeError("Pages.next_url(next_url=) must be callable")

    @property
    def fields(self) -> tuple[str, ...]:
        return ()


type Pagination[T] = NumberedPages[T] | OffsetPages[T] | CursorPages[T] | NextUrlPages[T]
"""Every strategy an operation may declare as ``__pages__``."""


class Pages:
    """The public entry for pagination strategies, assigned to ``__pages__``."""

    __slots__ = ()

    @staticmethod
    def numbered[T](
        result: type[T],
        *,
        page: str,
        items: Callable[[T], Sequence[object]],
        size: str | None = None,
        total_pages: Callable[[T], int] | None = None,
    ) -> NumberedPages[T]:
        return NumberedPages(
            result=result, page=page, items=items, size=size, total_pages=total_pages
        )

    @staticmethod
    def offset[T](
        result: type[T],
        *,
        offset: str,
        items: Callable[[T], Sequence[object]],
        limit: str | None = None,
        total: Callable[[T], int] | None = None,
    ) -> OffsetPages[T]:
        return OffsetPages(result=result, offset=offset, items=items, limit=limit, total=total)

    @staticmethod
    def cursor[T](
        result: type[T],
        *,
        cursor: str,
        items: Callable[[T], Sequence[object]],
        next_cursor: Callable[[T], object | None],
    ) -> CursorPages[T]:
        return CursorPages(result=result, cursor=cursor, items=items, next_cursor=next_cursor)

    @staticmethod
    def next_url[T](
        result: type[T],
        *,
        items: Callable[[T], Sequence[object]],
        next_url: Callable[[T], str | None],
    ) -> NextUrlPages[T]:
        return NextUrlPages(result=result, items=items, next_url=next_url)


@dataclass(frozen=True, slots=True)
class NextUrl:
    """What :func:`next_changes` answers for a link strategy: send the same request there."""

    url: str


def next_changes[T](
    strategy: Pagination[T],
    request: object,
    result: T,
    *,
    fresh: int,
) -> dict[str, object] | NextUrl | None:
    """Field changes for the next request, a :class:`NextUrl`, or ``None`` when it is over.

    ``fresh`` is how many elements of this page the caller used: zero after ``key=``
    deduplication means the server has nothing new, even when the page itself is not empty.
    Every strategy stops on it first; the rest of the rules are the strategy's own, applied
    in the order its function documents, and a test pins each order.
    """

    if fresh <= 0:
        return None
    if isinstance(strategy, NumberedPages):
        return _next_numbered(strategy, request, result)
    if isinstance(strategy, OffsetPages):
        return _next_offset(strategy, request, result)
    if isinstance(strategy, CursorPages):
        return _next_cursor(strategy, result)
    return _next_link(strategy, result)


def _int_field(request: object, name: str) -> int:
    value = getattr(request, name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PlanError(
            f"{type(request).__name__}.{name} must be an int to paginate, "
            f"got {type(value).__name__}"
        )
    return value


def _declared_int(request: object, name: str | None) -> int | None:
    """An optional size field: its ``int`` value, or ``None`` when absent or not an int."""

    if name is None:
        return None
    value = getattr(request, name)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _next_numbered[T](
    strategy: NumberedPages[T], request: object, result: T
) -> dict[str, object] | None:
    """2. ``total_pages`` reached → stop; 3. page shorter than ``size`` → stop; 4. page + 1."""

    current = _int_field(request, strategy.page)
    if strategy.total_pages is not None and current >= strategy.total_pages(result):
        return None
    size = _declared_int(request, strategy.size)
    if size is not None and len(strategy.items(result)) < size:
        return None
    return {strategy.page: current + 1}


def _next_offset[T](
    strategy: OffsetPages[T], request: object, result: T
) -> dict[str, object] | None:
    """2. ``total`` reached → stop; 3. page shorter than ``limit`` → stop; 4. offset + len."""

    current = _int_field(request, strategy.offset)
    count = len(strategy.items(result))
    if strategy.total is not None and current + count >= strategy.total(result):
        return None
    limit = _declared_int(request, strategy.limit)
    if limit is not None and count < limit:
        return None
    return {strategy.offset: current + count}


def _next_cursor[T](strategy: CursorPages[T], result: T) -> dict[str, object] | None:
    """2. no ``next_cursor`` in the result → stop; 3. the same request carrying that token."""

    token = strategy.next_cursor(result)
    if token is None:
        return None
    return {strategy.cursor: token}


def _next_link[T](strategy: NextUrlPages[T], result: T) -> NextUrl | None:
    """2. no ``next_url`` in the result → stop; 3. the same request, sent to that link."""

    link = strategy.next_url(result)
    if link is None:
        return None
    if not isinstance(link, str) or not link:
        raise PlanError(
            f"{strategy.result.__name__}: next_url must return a non-empty str or None, "
            f"got {type(link).__name__}"
        )
    return NextUrl(link)


def validate_declaration(
    strategy: object,
    *,
    operation_type: type[Any],
    field_names: Sequence[str],
    result_type: object,
) -> Pagination[Any]:
    """Check ``__pages__`` against the class it sits on; raise :class:`PlanError` at import."""

    name = operation_type.__name__
    if not isinstance(strategy, NumberedPages | OffsetPages | CursorPages | NextUrlPages):
        raise PlanError(
            f"operation class {name}.__pages__ must be a Pages strategy, "
            f"got {type(strategy).__name__}"
        )
    for field in strategy.fields:
        if field not in field_names:
            raise PlanError(
                f"{name}.__pages__ names unknown field {field!r}; fields: {', '.join(field_names)}"
            )
    if strategy.result is not result_type:
        declared = getattr(strategy.result, "__name__", repr(strategy.result))
        actual = getattr(result_type, "__name__", repr(result_type))
        raise PlanError(
            f"{name}.__pages__ declares result {declared}, the operation returns {actual}"
        )
    return strategy


def check_max_pages(max_pages: int | None) -> None:
    if max_pages is None:
        return
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be None or >= 1")
