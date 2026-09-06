"""One attempt, its budgets and the policies that decide whether another one happens.

Everything here is pure: a policy reads a state and an outcome and returns the next state,
the reason it chose it and the one effect the runner still has to perform. No transport, no
event loop, no exceptions used as control flow — so a retry rule can be tested by calling it.

The budget invariant the loop has always had, now stated once: **each policy spends only its
own budget, and the hard attempt limit is their sum.** A policy that is out of budget does
not decide; the classifier falls through to the next one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from eazy_sdk.core.kernel import ValuePatch
from eazy_sdk.handlers import TransportError


@dataclass(frozen=True, slots=True)
class AttemptBudgets:
    """What is left of each budget, and the hard limit that is their sum."""

    transport: int = 0
    auth: int = 0
    redirect: int = 0
    replays: Mapping[str, int] = field(default_factory=dict)
    hard_limit: int = 1
    """How many attempts the call may make at all: fixed when the call starts.

    It is the sum of every policy budget plus the attempts that belong to no policy — the
    first one, and any slack the caller left in ``max_attempts``. Spending a budget does not
    lower it, so a call that redirects still has its retries.
    """

    @classmethod
    def of(
        cls,
        *,
        max_attempts: int,
        transport_retries: int,
        auth_retries: int,
        max_redirects: int,
        replays: Mapping[str, int],
    ) -> AttemptBudgets:
        """The budgets of one call; ``max_attempts`` already covers the other three."""

        return cls(
            transport=transport_retries,
            auth=auth_retries,
            redirect=max_redirects,
            replays=dict(replays),
            hard_limit=max_attempts + sum(replays.values()),
        )

    @property
    def base(self) -> int:
        """Attempts the hard limit grants beyond every policy budget."""

        return (
            self.hard_limit
            - self.transport
            - self.auth
            - self.redirect
            - sum(self.replays.values())
        )

    def spend(self, name: str, *, policy: str | None = None) -> AttemptBudgets:
        if name == "replay":
            assert policy is not None
            remaining = dict(self.replays)
            remaining[policy] -= 1
            return replace(self, replays=remaining)
        return replace(self, **{name: getattr(self, name) - 1})


@dataclass(frozen=True, slots=True)
class AttemptState:
    """The whole of one attempt: where it goes, why it happens, what it may still spend."""

    number: int
    kind: str
    url: str
    reason: str = "first attempt"
    method: str | None = None
    omit_body: bool = False
    redirected: bool = False
    retries: int = 0
    budgets: AttemptBudgets = field(default_factory=AttemptBudgets)

    @classmethod
    def initial(cls, url: str, budgets: AttemptBudgets) -> AttemptState:
        return cls(number=1, kind="initial", url=url, budgets=budgets)

    def next(self, kind: str, **changes: Any) -> AttemptState:
        """The successor attempt: one higher, a new reason, everything else carried over."""

        return replace(self, number=self.number + 1, kind=kind, **changes)

    def effective_method(self, declared: str) -> str:
        return self.method or declared


@dataclass(frozen=True, slots=True)
class TransportFailure:
    """The handler raised: no response exists, and middleware may have proposed an action."""

    error: TransportError
    proposed: object | None = None
    idempotent: bool = True
    retries_configured: bool = False


@dataclass(frozen=True, slots=True)
class Continue:
    """Another attempt happens; its state already says why and what it may still spend."""

    state: AttemptState
    patch: ValuePatch | None = None
    wait_attempt: int | None = None
    refresh_auth: bool = False
    reaction: object | None = None

    @property
    def reason(self) -> str:
        return self.state.reason


@dataclass(frozen=True, slots=True)
class Stop[T]:
    """No further attempt: this is the result of the call."""

    result: T


@dataclass(frozen=True, slots=True)
class Fail:
    """No further attempt: the call raises."""

    error: BaseException


type Decision[T] = Continue | Stop[T] | Fail


class TransportRetryPolicy:
    """Replays a transport failure while its own budget lasts.

    A middleware that proposes an action overrides the budget: the attempt is repeated even
    when the retry budget is exhausted, exactly as the loop has always done. Either way a
    non-idempotent operation is never replayed.
    """

    def decide(self, state: AttemptState, outcome: TransportFailure) -> Decision[Any]:
        if state.budgets.transport <= 0 and outcome.proposed is None:
            return Fail(outcome.error)
        if not outcome.idempotent:
            if outcome.retries_configured:
                from eazy_sdk.clients.base import UnsafeReplayError

                return Fail(
                    UnsafeReplayError("retry policy requires an idempotent operation")
                )
            return Fail(outcome.error)
        if state.budgets.transport <= 0:
            return Continue(
                state.next("transport-retry", reason="middleware proposed a replay")
            )
        return Continue(
            state.next(
                "transport-retry",
                reason="transport error, retry budget",
                budgets=state.budgets.spend("transport"),
                retries=state.retries + 1,
            ),
            wait_attempt=state.retries + 1,
        )


class ResponseRetryPolicy:
    """Replays a response the retry policy rejects, spending the same transport budget."""

    def decide(
        self,
        state: AttemptState,
        kind: str,
        *,
        patch: ValuePatch | None,
        consumes_transport: bool,
    ) -> Decision[Any]:
        if not consumes_transport:
            return Continue(
                state.next(kind, reason="middleware proposed a replay"), patch=patch
            )
        return Continue(
            state.next(
                kind,
                reason="retryable response status",
                budgets=state.budgets.spend("transport"),
                retries=state.retries + 1,
            ),
            patch=patch,
            wait_attempt=state.retries + 1,
        )


class RedirectPolicy:
    """Follows a redirect on its own budget, carrying the new URL, method and body rule."""

    def decide(
        self,
        state: AttemptState,
        url: str,
        method: str | None,
        omit_body: bool,
    ) -> Decision[Any]:
        return Continue(
            state.next(
                "redirect",
                reason="redirect response",
                budgets=state.budgets.spend("redirect"),
                url=url,
                redirected=True,
                method=method if method is not None else state.method,
                omit_body=state.omit_body or omit_body,
            )
        )


class AuthRefreshPolicy:
    """Refreshes the selected security alternative once per unit of its own budget."""

    def decide(self, state: AttemptState) -> Decision[Any]:
        return Continue(
            state.next(
                "auth-refresh",
                reason="authentication rejected",
                budgets=state.budgets.spend("auth"),
            ),
            refresh_auth=True,
        )


class ProtectionPolicy:
    """Replays a challenged attempt while the matching guard has replays of its own left."""

    def decide(self, state: AttemptState, policy: str, match: object) -> Decision[Any]:
        return Continue(
            state.next(
                "reaction",
                reason=f"protection challenge: {policy}",
                budgets=state.budgets.spend("replay", policy=policy),
            ),
            reaction=match,
        )


__all__: list[str] = []
