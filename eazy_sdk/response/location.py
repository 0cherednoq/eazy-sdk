"""Where a response points: the ``Location`` header, compared part by part.

A service that reports the outcome of a call in a redirect says it in the target address: the
host and path it sends the client to, and the query it attaches. ``Location`` states which of
those a case expects, and every part is optional::

    from eazy_sdk.response import Bytes, Location

    signed_in = Location(path="/inbox*")
    banned = Location(query={"errno": "25"})
    rejected = Location(query={"fail": ...})        # the parameter is there, whatever it holds
    anywhere = Location()                           # the header is there, whatever it holds

    success = {302: Bytes(when=signed_in)}

It is an ordinary ``ResponseCondition`` and combines with the predicates of
:mod:`eazy_sdk.response.match` through ``&``, ``|`` and ``~``. The parts given are all required
at once; "either" is two cases, or ``Location(...) | Location(...)``.

A string in ``host`` or ``path`` is a glob in which only ``*`` is special and stands for any run
of characters, ``/`` included. ``?`` and ``[`` are literal, because addresses carry them
literally. A compiled pattern is matched against the whole part.

A relative header is resolved against the address the request went to before anything is
compared, so the same declaration reads ``/inbox`` and ``https://mail.example/inbox`` alike. A
response that carries no ``Location``, or more than one, points nowhere in particular and matches
no pattern: that is another case, never an error.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import EllipsisType
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, parse_qs, urljoin, urlsplit

from eazy_sdk.core.errors import PlanError
from eazy_sdk.response.match import Predicate, _label_of

if TYPE_CHECKING:
    from eazy_sdk.response.cases import ResponseContext

__all__ = ["Location", "ResolvedLocation", "resolved_location"]

type LocationPart = str | re.Pattern[str]
type LocationQueryValue = str | re.Pattern[str] | EllipsisType

_HEADER = "location"


@dataclass(frozen=True, slots=True)
class ResolvedLocation:
    """One response's target: the absolute address and the pieces a pattern compares."""

    url: str
    host: str
    """Lowercased and without the port; empty when the address names no host."""
    path: str
    """As received, not decoded; ``/`` when the address names no path."""
    query: Mapping[str, tuple[str, ...]]
    """Decoded values by name, a parameter repeated keeps every value in order."""


def resolved_location(context: ResponseContext[object]) -> ResolvedLocation | None:
    """The target of this response, read once however many cases ask; ``None`` for no target."""

    return context.cached(_HEADER_KEY, lambda: _resolve(context))


class _HeaderKey:
    """The cache key of the resolved header: one per process, equal only to itself."""

    __slots__ = ()


_HEADER_KEY = _HeaderKey()


def _resolve(context: ResponseContext[object]) -> ResolvedLocation | None:
    lines = context.headers.getall(_HEADER)
    if len(lines) != 1:
        return None
    try:
        url = urljoin(context.response.url, lines[0].strip())
        split: SplitResult = urlsplit(url)
        host = split.hostname or ""
    except ValueError:  # an address that does not parse points nowhere
        return None
    query = {
        name: tuple(values)
        for name, values in parse_qs(split.query, keep_blank_values=True).items()
    }
    return ResolvedLocation(url, host.lower(), split.path or "/", query)


def _glob(pattern: str, *, ignore_case: bool) -> re.Pattern[str]:
    """``*`` is any run of characters; everything else is itself."""

    source = "".join(".*" if char == "*" else re.escape(char) for char in pattern)
    return re.compile(source, re.DOTALL | (re.IGNORECASE if ignore_case else 0))


def _checked_part(name: str, value: object) -> LocationPart | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    if isinstance(value, re.Pattern) and isinstance(value.pattern, str):
        return value
    raise PlanError(
        f"Location({name}=...) takes a non-empty string or a compiled str pattern, not {value!r}"
    )


def _checked_query(
    value: object,
) -> tuple[tuple[str, LocationQueryValue], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise PlanError(f"Location(query=...) takes a mapping of parameter names, not {value!r}")
    output: list[tuple[str, LocationQueryValue]] = []
    for name, expected in value.items():
        if not isinstance(name, str) or not name:
            raise PlanError(f"Location(query=...) names a parameter with {name!r}")
        if not (
            expected is ...
            or isinstance(expected, str)
            or (isinstance(expected, re.Pattern) and isinstance(expected.pattern, str))
        ):
            raise PlanError(
                f"Location(query={{{name!r}: ...}}) takes a string, a compiled str pattern "
                f"or ... for any value, not {expected!r}"
            )
        output.append((name, expected))
    return tuple(output)


def _part_text(value: LocationPart) -> str:
    return f"matches {value.pattern!r}" if isinstance(value, re.Pattern) else repr(value)


class Location:
    """The parts of the ``Location`` header a case expects; every one of them is optional."""

    __slots__ = ("_contains", "_host", "_host_test", "_path", "_path_test", "_query")

    def __init__(
        self,
        *,
        host: LocationPart | None = None,
        path: LocationPart | None = None,
        query: Mapping[str, LocationQueryValue] | None = None,
        contains: str | None = None,
    ) -> None:
        self._host = _checked_part("host", host)
        self._path = _checked_part("path", path)
        self._query = _checked_query(query)
        if contains is not None and (not isinstance(contains, str) or not contains):
            raise PlanError(f"Location(contains=...) takes a non-empty string, not {contains!r}")
        self._contains = contains
        self._host_test = (
            _glob(self._host, ignore_case=True) if isinstance(self._host, str) else self._host
        )
        self._path_test = (
            _glob(self._path, ignore_case=False) if isinstance(self._path, str) else self._path
        )

    @property
    def label(self) -> str:
        """How the pattern reads in a diagnostic."""

        parts: list[str] = []
        if self._host is not None:
            parts.append(f"host {_part_text(self._host)}")
        if self._path is not None:
            parts.append(f"path {_part_text(self._path)}")
        for name, expected in self._query:
            parts.append(
                f"query {name!r} is present"
                if expected is ...
                else f"query {name!r} {_part_text(expected)}"
            )
        if self._contains is not None:
            parts.append(f"contains {self._contains!r}")
        return "location " + (" and ".join(parts) if parts else "is present")

    def __call__(self, context: ResponseContext[object]) -> bool:
        target = resolved_location(context)
        return target is not None and self.holds(target)

    def holds(self, target: ResolvedLocation) -> bool:
        """Whether an already resolved target is the one this pattern describes."""

        if self._host_test is not None and self._host_test.fullmatch(target.host) is None:
            return False
        if self._path_test is not None and self._path_test.fullmatch(target.path) is None:
            return False
        for name, expected in self._query:
            values = target.query.get(name)
            if values is None:
                return False
            if expected is ...:
                continue
            if isinstance(expected, str):
                if expected not in values:
                    return False
            elif not any(expected.fullmatch(value) for value in values):
                return False
        return self._contains is None or self._contains in target.url

    def __and__(self, other: Callable[[ResponseContext[object]], bool]) -> Predicate:
        return Predicate(
            lambda context: self(context) and bool(other(context)),
            f"({self.label} and {_label_of(other)})",
        )

    def __or__(self, other: Callable[[ResponseContext[object]], bool]) -> Predicate:
        return Predicate(
            lambda context: self(context) or bool(other(context)),
            f"({self.label} or {_label_of(other)})",
        )

    def __invert__(self) -> Predicate:
        return Predicate(lambda context: not self(context), f"not {self.label}")

    def _identity(self) -> tuple[object, ...]:
        return (self._host, self._path, frozenset(self._query), self._contains)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Location) and self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __repr__(self) -> str:
        return f"<{self.label}>"
