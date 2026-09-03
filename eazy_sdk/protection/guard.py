"""One-class response guard: detect, solve, apply, cache."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.response import ResponseContext

from .advanced import (
    GuardCache,
    ProtectionBundle,
    ProtectionConfigurationError,
    ReplayPolicy,
    SolveContext,
    challenge_guard,
    safe_method,
    solution_cookie_set,
    solution_fields,
)


def host(name: str, *more: str) -> RequestScope:
    """Scope a guard to one or more exact hosts (``host:port`` when the port is explicit)."""

    hosts = (name, *more)
    if not all(isinstance(item, str) and item for item in hosts):
        raise TypeError("host scope requires non-empty host names")
    return RequestScope(hosts=frozenset(hosts))


def operation(value: object, *more: object) -> RequestScope:
    """Scope a guard to one or more operation ids or decorated API methods."""

    identities: list[str] = []
    for item in (value, *more):
        declaration = getattr(item, "declaration", None)
        source = declaration if declaration is not None else item
        operation_id = getattr(source, "operation_id", item)
        if not isinstance(operation_id, str) or not operation_id:
            raise TypeError(
                "operation scope requires an operation id or decorated API method"
            )
        identities.append(operation_id)
    return RequestScope(operation_ids=frozenset(identities))


@dataclass(frozen=True, slots=True)
class _CookieItem:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class GuardSolution:
    """Complete solution batch produced by :meth:`Guard.solution`.

    ``cookies`` are applied as a managed dynamic cookie set. ``headers``, ``query`` and
    ``body`` values are applied to destinations the guard declared on its class. ``expires_at``
    bounds session caching; ``None`` means "until the origin rejects it".
    """

    cookies: tuple[_CookieItem, ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    query: tuple[tuple[str, str], ...] = ()
    body: tuple[tuple[str, object], ...] = ()
    expires_at: datetime | None = None

    def __repr__(self) -> str:
        return (
            "GuardSolution("
            f"cookies={[item.name for item in self.cookies]!r}, "
            f"headers={[name for name, _ in self.headers]!r}, "
            f"query={[name for name, _ in self.query]!r}, "
            f"body={[name for name, _ in self.body]!r}, "
            f"expires_at={self.expires_at!r})"
        )

    def __getattr__(self, item: str) -> object:
        # Declared destinations are selected by the executor through synthetic field names
        # (``header:<name>`` etc.), which keeps GuardSolution a plain frozen value.
        kind, separator, name = item.partition(":")
        if separator:
            collection: tuple[tuple[str, object], ...] | None = {
                "header": self.headers,
                "query": self.query,
                "body": self.body,
            }.get(kind)
            if collection is not None:
                for candidate, value in collection:
                    if candidate == name:
                        return value
                raise AttributeError(f"guard solution has no {kind} value for {name!r}")
        raise AttributeError(item)


class Guard[TChallenge]:
    """Subclass, override ``detect()`` and ``solve()``, install with ``ClientConfig(guards=[...])``.

    Class attributes configure the guard: ``scope`` (default: every request of the client),
    ``cache`` (default ``"session"``), ``replay`` (default ``safe_method(max_replays=1)``),
    ``revision`` and the declared ``headers``/``query``/``body`` destinations. Cookies need no
    declaration. ``solve()`` may be synchronous or asynchronous and must return
    ``self.solution(...)``.
    """

    name: ClassVar[str | None] = None
    scope: ClassVar[RequestScope] = RequestScope()
    cache: ClassVar[GuardCache] = "session"
    replay: ClassVar[ReplayPolicy] = safe_method(max_replays=1)
    revision: ClassVar[int] = 1
    headers: ClassVar[tuple[str, ...]] = ()
    query: ClassVar[tuple[str, ...]] = ()
    body: ClassVar[tuple[str, ...]] = ()

    @property
    def identity(self) -> str:
        """Policy identity used for managed state and ``invalidate_protection()``."""

        return self.name or type(self).__name__

    def detect(self, response: ResponseContext[object]) -> TChallenge | None:
        raise NotImplementedError(f"{type(self).__name__}.detect() is not implemented")

    def solve(
        self,
        challenge: TChallenge,
        context: SolveContext,
    ) -> GuardSolution | Awaitable[GuardSolution]:
        raise NotImplementedError(f"{type(self).__name__}.solve() is not implemented")

    def solution(
        self,
        *,
        cookies: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, object] | None = None,
        expires_in: float | timedelta | None = None,
        expires_at: datetime | None = None,
    ) -> GuardSolution:
        """Build the solution batch; undeclared header/query/body names are rejected."""

        if expires_in is not None and expires_at is not None:
            raise ValueError("pass either expires_in or expires_at, not both")
        if expires_in is not None:
            delta = (
                expires_in
                if isinstance(expires_in, timedelta)
                else timedelta(seconds=expires_in)
            )
            if delta <= timedelta(0):
                raise ValueError("expires_in must be positive")
            expires_at = datetime.now(UTC) + delta
        elif expires_at is not None and expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return GuardSolution(
            cookies=tuple(_CookieItem(name, value) for name, value in (cookies or {}).items()),
            headers=tuple(_declared("header", headers, self.headers).items()),
            query=tuple(_declared("query", query, self.query).items()),
            body=tuple(_declared("body", body, self.body).items()),
            expires_at=expires_at,
        )

    def to_bundle(self) -> ProtectionBundle:
        return challenge_guard(
            name=self.identity,
            scope=self.scope,
            detect=self.detect,
            solver=self.solve,
            apply=solution_fields(
                headers={name: f"header:{name}" for name in self.headers} or None,
                query={name: f"query:{name}" for name in self.query} or None,
                body={name: f"body:{name}" for name in self.body} or None,
                cookie_set=solution_cookie_set(),
            ),
            cache=self.cache,
            replay=self.replay,
            revision=self.revision,
        ).to_bundle()


def _declared[T](
    kind: str,
    provided: Mapping[str, T] | None,
    declared: tuple[str, ...],
) -> dict[str, T]:
    values = dict(provided or {})
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ProtectionConfigurationError(
            f"guard solution sets undeclared {kind} destination(s): {', '.join(unknown)}; "
            f"declare them in the guard's `{kind if kind != 'header' else 'headers'}` attribute"
        )
    return values


__all__: list[str] = [
    "Guard",
    "GuardSolution",
    "host",
    "operation",
]

