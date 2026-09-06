"""Phase 45: the attempt state machine, its budgets and its four policies.

Nothing in the first half of this file touches a client, a handler or an event loop: a
policy is a function from a state and an outcome to the next state. The second half proves
the invariant the loop must keep — every attempt prepares and signs again.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys
from typing import Annotated, Any

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import (
    AsyncApi,
    AsyncClient,
    ClientConfig,
    Identity,
    Json,
    Resilience,
    Responses,
    RetryPolicy,
    Success,
    api,
)
from eazy_sdk.clients.attempts import (
    AttemptBudgets,
    AttemptState,
    AuthRefreshPolicy,
    Continue,
    Fail,
    ProtectionPolicy,
    RedirectPolicy,
    ResponseRetryPolicy,
    TransportFailure,
    TransportRetryPolicy,
)
from eazy_sdk.clients.base import UnsafeReplayError
from eazy_sdk.handlers import TransportError
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import (
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    header_output,
    hmac_sha256,
)
from eazy_sdk.request.markers import JsonBody

BASE = "https://attempts.test"


def _state(**budgets: Any) -> AttemptState:
    return AttemptState.initial(f"{BASE}/x", AttemptBudgets(**budgets))


def test_the_hard_limit_is_the_sum_of_every_budget_and_never_shrinks() -> None:
    budgets = AttemptBudgets.of(
        max_attempts=1 + 2 + 1 + 1,
        transport_retries=2,
        auth_retries=1,
        max_redirects=1,
        replays={"guard": 3},
    )
    assert budgets.hard_limit == 5 + 3
    assert budgets.base == 1
    spent = budgets.spend("transport").spend("auth").spend("replay", policy="guard")
    assert spent.hard_limit == budgets.hard_limit
    assert (spent.transport, spent.auth, spent.replays["guard"]) == (1, 0, 2)
    assert budgets.transport == 2, "budgets are immutable"


def test_an_attempt_state_transition_is_a_new_state() -> None:
    state = _state(transport=1)
    following = state.next("transport-retry", budgets=state.budgets.spend("transport"))
    assert (following.number, following.kind) == (2, "transport-retry")
    assert (state.number, state.kind) == (1, "initial")
    assert following.url == state.url
    assert state.effective_method("POST") == "POST"
    assert following.next("redirect", method="GET").effective_method("POST") == "GET"


def test_transport_retry_spends_its_own_budget_and_asks_for_the_backoff() -> None:
    state = _state(transport=1)
    decision = TransportRetryPolicy().decide(
        state, TransportFailure(TransportError("t", "emit", 1, OSError("down")))
    )
    assert isinstance(decision, Continue)
    assert decision.state.kind == "transport-retry"
    assert decision.state.budgets.transport == 0
    assert decision.wait_attempt == 1
    assert decision.reason


def test_transport_retry_stops_when_its_budget_is_gone() -> None:
    error = TransportError("t", "emit", 1, OSError("down"))
    decision = TransportRetryPolicy().decide(_state(transport=0), TransportFailure(error))
    assert isinstance(decision, Fail)
    assert decision.error is error


def test_transport_retry_never_replays_a_non_idempotent_operation() -> None:
    error = TransportError("t", "emit", 1, OSError("down"))
    policy = TransportRetryPolicy()
    silent = policy.decide(_state(transport=1), TransportFailure(error, idempotent=False))
    assert isinstance(silent, Fail) and silent.error is error
    loud = policy.decide(
        _state(transport=1),
        TransportFailure(error, idempotent=False, retries_configured=True),
    )
    assert isinstance(loud, Fail) and isinstance(loud.error, UnsafeReplayError)


def test_a_middleware_proposal_replays_past_an_exhausted_retry_budget() -> None:
    decision = TransportRetryPolicy().decide(
        _state(transport=0),
        TransportFailure(TransportError("t", "emit", 1, OSError("down")), proposed=object()),
    )
    assert isinstance(decision, Continue)
    assert decision.state.budgets.transport == 0
    assert decision.wait_attempt is None


def test_response_retry_shares_the_transport_budget_only_when_it_consumes_it() -> None:
    state = _state(transport=2)
    spent = ResponseRetryPolicy().decide(
        state, "response-retry", patch=None, consumes_transport=True
    )
    assert isinstance(spent, Continue)
    assert spent.state.budgets.transport == 1
    assert spent.wait_attempt == 1
    free = ResponseRetryPolicy().decide(
        state, "middleware-retry", patch=None, consumes_transport=False
    )
    assert isinstance(free, Continue)
    assert free.state.budgets.transport == 2
    assert free.wait_attempt is None


def test_a_redirect_carries_its_method_and_body_rule_forward() -> None:
    state = _state(redirect=2)
    first = RedirectPolicy().decide(state, f"{BASE}/one", "GET", True)
    assert isinstance(first, Continue)
    assert first.state.budgets.redirect == 1
    assert (first.state.url, first.state.method, first.state.omit_body) == (
        f"{BASE}/one",
        "GET",
        True,
    )
    assert first.state.redirected
    second = RedirectPolicy().decide(first.state, f"{BASE}/two", None, False)
    assert isinstance(second, Continue)
    assert (second.state.method, second.state.omit_body) == ("GET", True)


def test_auth_refresh_and_protection_each_spend_only_their_own_budget() -> None:
    state = _state(transport=1, auth=1, redirect=1, replays={"guard": 1})
    refresh = AuthRefreshPolicy().decide(state)
    assert isinstance(refresh, Continue)
    assert refresh.refresh_auth
    assert refresh.state.budgets.auth == 0
    assert (refresh.state.budgets.transport, refresh.state.budgets.redirect) == (1, 1)

    match = object()
    reaction = ProtectionPolicy().decide(state, "guard", match)
    assert isinstance(reaction, Continue)
    assert reaction.reaction is match
    assert reaction.state.budgets.replays["guard"] == 0
    assert reaction.state.budgets.auth == 1
    assert "guard" in reaction.reason


def test_the_policies_reach_no_transport() -> None:
    module = sys.modules["eazy_sdk.clients.attempts"]
    source = pathlib.Path(inspect.getfile(module)).read_text(encoding="utf-8")
    imported = {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = ("handlers.httpx", "handlers.requests", "handlers.curl_cffi", "zapros")
    assert not [name for name in imported if name.startswith(forbidden)]
    assert "asyncio" not in {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }


def test_no_executor_method_is_longer_than_eighty_lines() -> None:
    source = pathlib.Path(
        inspect.getfile(sys.modules["eazy_sdk.clients.executor"])
    ).read_text(encoding="utf-8")
    long_ones = [
        (node.name, node.end_lineno - node.lineno + 1)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.end_lineno is not None
        and node.end_lineno - node.lineno + 1 > 80
    ]
    assert long_ones == []


class Receipt(BaseModel):
    attempt: int


RECEIPT: Responses[Receipt] = Responses(success=(Success(200, Json(Receipt)),))
PAYMENTS_KEY = SigningKeyRequirement("attempts")
SIGNATURE = hmac_sha256(
    key=PAYMENTS_KEY,
    base=body_digest("sha256"),
    output=header_output("X-Signature"),
)


class Payload(BaseModel):
    value: str


class RetriedApi(AsyncApi):
    @api.put("/pay", responses=RECEIPT, signing=SIGNATURE)
    async def pay(self, *, body: Annotated[Payload, JsonBody()]) -> Receipt:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_every_attempt_prepares_and_signs_again() -> None:
    signatures: list[str] = []
    keys = 0

    def key_provider(_requirement: SigningKeyRequirement) -> SigningKey:
        nonlocal keys
        keys += 1
        return SigningKey(f"secret-{keys}".encode())

    attempts = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        signatures.append(request.headers["X-Signature"])
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"attempt": attempts})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    config = ClientConfig(
        resilience=Resilience(retry=RetryPolicy.safe(max_attempts=3), auth_retries=0)
    )
    client = AsyncClient(
        base_url=BASE,
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=config,
    )
    async with client:
        receipt = await RetriedApi(client, identity=Identity(key_provider=key_provider)).pay(
            body=Payload(value="quiet")
        )

    assert receipt.attempt == 3
    assert keys == 3, "every attempt asked for a key again"
    assert len(set(signatures)) == 3, "every attempt signed the request again"


@pytest.mark.asyncio
async def test_the_observer_sees_why_each_attempt_happens_and_what_is_left() -> None:
    seen: list[dict[str, Any]] = []

    def observer(phase: str, value: object | None) -> None:
        if phase == "start_attempt":
            assert isinstance(value, dict)
            seen.append(value)

    attempts = 0

    async def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts < 2 else 200, json={"attempt": attempts})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    config = ClientConfig(
        resilience=Resilience(retry=RetryPolicy.safe(max_attempts=2), auth_retries=0)
    )
    client = AsyncClient(
        base_url=BASE,
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=config,
    )
    async with client:
        identity = Identity(key_provider=lambda _r: SigningKey(b"k"), observer=observer)
        await RetriedApi(client, identity=identity).pay(body=Payload(value="quiet"))

    assert [item["kind"] for item in seen] == ["initial", "response-retry"]
    assert seen[0]["reason"] == "first attempt"
    assert seen[1]["reason"] == "retryable response status"
    assert seen[0]["budgets"]["transport"] == 1
    assert seen[1]["budgets"]["transport"] == 0
