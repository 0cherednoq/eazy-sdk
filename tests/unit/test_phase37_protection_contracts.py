"""Phase 37: protection contracts — error names, single-flight per cache mode, fetch/deadline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from zapros import AsyncBaseHandler, Request, Response

from eazy_sdk import AsyncApi, AsyncClient, ClientConfig, api
from eazy_sdk.core.errors import PlanError
from eazy_sdk.core.kernel import OperationIdentity
from eazy_sdk.protection import Guard, GuardCache, GuardSolution, SolveContext, advanced, host
from eazy_sdk.protection.advanced import ChallengeParseError
from eazy_sdk.response import Json, ResponseContext, Responses, Success

BASE_URL = "https://phase37c.test"


@dataclass(frozen=True, slots=True)
class Challenge:
    revision: int


class ProtectedApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="phase37c.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(self) -> dict[str, object]:
        raise NotImplementedError


class Origin(AsyncBaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    async def ahandle(self, request: Request) -> Response:
        self.requests.append(request)
        if request.url.pathname == "/redirect":
            return Response(302, [("Location", f"{BASE_URL}/elsewhere")], request=request)
        if request.headers.get("X-Token") is None:
            return Response(
                403,
                [("Content-Type", "application/json")],
                content=b'{"revision":1}',
                request=request,
            )
        return Response(
            200,
            [("Content-Type", "application/json")],
            content=b'{"ok":true}',
            request=request,
        )

    async def aclose(self) -> None:
        return None


class SlowGuard(Guard[Challenge]):
    scope = host("phase37c.test")
    headers: ClassVar[tuple[str, ...]] = ("X-Token",)

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, response: ResponseContext[object]) -> Challenge | None:
        if response.response.status_code != 403:
            return None
        return Challenge(1)

    async def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
        self.calls += 1
        await asyncio.sleep(0.02)
        return self.solution(headers={"X-Token": f"t{self.calls}"})


def test_runtime_parse_error_and_detector_error_are_distinct_names() -> None:
    assert "ChallengeParseError" in advanced.__all__
    assert "ChallengeMalformedError" not in advanced.__all__
    assert not hasattr(advanced, "ChallengeMalformedError")
    assert issubclass(ChallengeParseError, PlanError)
    assert issubclass(advanced.MalformedChallengeError, ValueError)
    error = ChallengeParseError("policy", 2)
    assert error.policy == "policy" and error.attempt == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("cache", "expected_solves"), [("session", 1), ("call", 3), ("none", 3)])
async def test_only_session_cache_single_flights_concurrent_calls(
    cache: GuardCache, expected_solves: int
) -> None:
    guard = type("CachedGuard", (SlowGuard,), {"cache": cache})()
    origin = Origin()
    async with AsyncClient(
        base_url=BASE_URL, handler=origin, config=ClientConfig(guards=[guard])
    ) as client:
        service = ProtectedApi(client)
        results = await asyncio.gather(*(service.protected() for _ in range(3)))
    assert results == [{"ok": True}] * 3
    assert guard.calls == expected_solves


def test_remaining_is_the_solve_budget_left() -> None:
    operation = OperationIdentity("phase37c.protected")
    without = SolveContext(operation, None, 1)
    assert without.deadline is None and without.remaining() is None
    ahead = SolveContext(operation, None, 1, deadline=datetime.now(UTC) + timedelta(seconds=30))
    left = ahead.remaining()
    assert left is not None and timedelta(seconds=25) < left <= timedelta(seconds=30)
    behind = SolveContext(operation, None, 1, deadline=datetime.now(UTC) - timedelta(seconds=1))
    assert behind.remaining() == timedelta(0)


@pytest.mark.asyncio
async def test_fetch_returns_redirects_as_is_and_carries_the_full_budget() -> None:
    class FetchingGuard(SlowGuard):
        def __init__(self) -> None:
            super().__init__()
            self.redirect_status: int | None = None
            self.remaining: timedelta | None = None

        async def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
            self.remaining = context.remaining()
            probe = await context.fetch(f"{BASE_URL}/redirect")
            self.redirect_status = probe.status_code
            return await super().solve(challenge, context)

    guard = FetchingGuard()
    origin = Origin()
    async with AsyncClient(
        base_url=BASE_URL,
        handler=origin,
        config=ClientConfig(guards=[guard], timeout=5.0),
    ) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}
    assert guard.redirect_status == 302
    assert guard.remaining is not None and timedelta(seconds=4) < guard.remaining
    paths = [request.url.pathname for request in origin.requests]
    assert paths == ["/protected", "/redirect", "/protected"]
