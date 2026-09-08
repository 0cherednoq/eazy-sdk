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

__all__ = ["NumberedPages", "Pages", "Pagination", "next_changes"]


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


type Pagination[T] = NumberedPages[T]
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


def next_changes[T](
    strategy: Pagination[T],
    request: object,
    result: T,
    *,
    fresh: int,
) -> dict[str, object] | None:
    """Field changes for the next request, or ``None`` when the iteration is over.

    ``fresh`` is how many elements of this page the caller used: zero after ``key=``
    deduplication means the server has nothing new, even when the page itself is not empty.
    The rules apply in this order, and a test pins it:

    1. no fresh elements → stop;
    2. ``total_pages`` declared and the current page reached it → stop;
    3. ``size`` declared, the request carries an ``int`` there, and the page is shorter → stop;
    4. otherwise the same request with the page number incremented.
    """

    if fresh <= 0:
        return None
    current = getattr(request, strategy.page)
    if not isinstance(current, int) or isinstance(current, bool):
        raise PlanError(
            f"{type(request).__name__}.{strategy.page} must be an int to paginate, "
            f"got {type(current).__name__}"
        )
    if strategy.total_pages is not None and current >= strategy.total_pages(result):
        return None
    if strategy.size is not None:
        size = getattr(request, strategy.size)
        if (
            isinstance(size, int)
            and not isinstance(size, bool)
            and len(strategy.items(result)) < size
        ):
            return None
    return {strategy.page: current + 1}


def validate_declaration(
    strategy: object,
    *,
    operation_type: type[Any],
    field_names: Sequence[str],
    result_type: object,
) -> Pagination[Any]:
    """Check ``__pages__`` against the class it sits on; raise :class:`PlanError` at import."""

    name = operation_type.__name__
    if not isinstance(strategy, NumberedPages):
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
