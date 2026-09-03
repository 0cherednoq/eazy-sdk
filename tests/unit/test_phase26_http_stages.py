from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from eazy_sdk.clients._http_stages import (
    AuthRefreshTransition,
    ReactionTransition,
    RedirectTransition,
    RejectedResponse,
    ResponseDecisionInput,
    RetryTransition,
    TerminalResponse,
    decide_response,
)
from eazy_sdk.clients.base import RedirectLimitError, UnsafeReplayError
from eazy_sdk.core import (
    ValuePatch,
)
from eazy_sdk.middleware import RedirectTo, RetryAttempt
from eazy_sdk.protection.advanced import SignalMatch
from eazy_sdk.response import Headers, Json, NormalizedResponse, Success
from eazy_sdk.response.cases import SuccessOutcome, UnexpectedOutcome


def _response(status: int = 200, *, location: str | None = None) -> NormalizedResponse[object]:
    headers = Headers((("location", location),)) if location is not None else Headers()
    return NormalizedResponse(status, "https://api.test/source", "GET", headers, b"{}")


def _success() -> SuccessOutcome[dict[str, bool]]:
    return SuccessOutcome(
        {"ok": True},
        Success(200, Json(dict)),
        cast(Any, None),
    )


def _stage(**changes: Any) -> ResponseDecisionInput[dict[str, bool]]:
    base = ResponseDecisionInput(
        response=_response(),
        proposed=None,
        signal=None,
        outcome=_success(),
        idempotent=True,
        attempt=1,
        hard_attempt_limit=2,
        transport_remaining=1,
        retry_statuses=frozenset({503}),
        redirect_remaining=1,
        auth_remaining=1,
        auth_refreshable=True,
        current_url="https://api.test/source",
        effective_method="GET",
        raw_response=False,
    )
    return replace(base, **changes)


def test_response_stage_preserves_transition_precedence() -> None:
    signal = SignalMatch(cast(Any, object()), object(), cast(Any, None))

    middleware = decide_response(
        _stage(
            proposed=RetryAttempt(ValuePatch(()), kind="middleware"),
            response=_response(503),
            signal=signal,
        )
    )
    assert middleware == RetryTransition("middleware", ValuePatch(()))

    redirect = decide_response(_stage(proposed=RedirectTo("/middleware")))
    assert redirect == RedirectTransition("https://api.test/middleware")

    retry = decide_response(_stage(response=_response(503)))
    assert retry == RetryTransition("response-retry", consumes_transport=True)

    reaction = decide_response(_stage(signal=signal))
    assert reaction == ReactionTransition(signal)

    auth = decide_response(_stage(response=_response(401)))
    assert auth == AuthRefreshTransition()


def test_response_stage_preserves_redirect_and_terminal_materialization() -> None:
    redirect = decide_response(
        _stage(
            response=_response(302, location="/next"),
            effective_method="POST",
        )
    )
    assert redirect == RedirectTransition("https://api.test/next", "GET", True)

    terminal = decide_response(_stage())
    assert isinstance(terminal, TerminalResponse)
    assert terminal.value == {"ok": True}

    raw_response = _response(204)
    raw = decide_response(_stage(response=raw_response, outcome=None, raw_response=True))
    assert isinstance(raw, TerminalResponse)
    assert cast(object, raw.value) is raw_response
    assert raw.response is raw_response

    unexpected = UnexpectedOutcome((), cast(Any, None))
    rejected = decide_response(_stage(outcome=unexpected))
    assert rejected == RejectedResponse(unexpected)


def test_response_stage_rejects_unsafe_replay_and_exhausted_redirect() -> None:
    with pytest.raises(UnsafeReplayError, match="retry policy"):
        decide_response(_stage(response=_response(503), idempotent=False))
    with pytest.raises(UnsafeReplayError, match="auth refresh"):
        decide_response(_stage(response=_response(401), idempotent=False))
    with pytest.raises(RedirectLimitError, match="redirect budget"):
        decide_response(
            _stage(
                response=_response(302, location="/next"),
                redirect_remaining=0,
            )
        )
