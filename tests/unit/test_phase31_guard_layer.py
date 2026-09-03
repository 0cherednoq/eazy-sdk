"""Phase 31: simple guard layer (Guard, challenge_guard(cache=), guards=, invalidate)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import pytest
from zapros import AsyncBaseHandler, BaseHandler, Request, Response

from eazy_sdk import AsyncApi, AsyncClient, Client, ClientConfig, SyncApi, api
from eazy_sdk.ext import RequestScope
from eazy_sdk.protection import (
    ChallengeSolveError,
    Guard,
    GuardSolution,
    ProtectionConfigurationError,
    SolveContext,
    challenge_guard,
    host,
    operation,
    solution_fields,
)
from eazy_sdk.protection.advanced import (
    ProtectionPersistenceMode,
    ProtectionStateScope,
    _ChallengeGuard,
)
from eazy_sdk.request import Header
from eazy_sdk.response import Json, ResponseContext, Responses, Success

BASE_URL = "https://phase31.test"


@dataclass(frozen=True, slots=True)
class Challenge:
    revision: int


class ProtectedApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="phase31.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(
        self,
        *,
        user_agent: Annotated[str, Header("User-Agent")] = "ua",
    ) -> dict[str, object]:
        raise NotImplementedError


class SyncProtectedApi(SyncApi):
    @api.get(
        "/protected",
        operation_id="phase31.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def protected(self) -> dict[str, object]:
        raise NotImplementedError


def _respond(request: Request) -> Response:
    cookie = request.headers.get("Cookie") or ""
    token = request.headers.get("X-Token")
    if "clearance=" not in cookie and token is None:
        return Response(
            403,
            [("Content-Type", "application/json")],
            content=b'{"revision":3}',
            request=request,
        )
    return Response(
        200,
        [("Content-Type", "application/json")],
        content=b'{"ok":true}',
        request=request,
    )


class OriginHandler(AsyncBaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    async def ahandle(self, request: Request) -> Response:
        self.requests.append(request)
        return _respond(request)

    async def aclose(self) -> None:
        return None


class SyncOriginHandler(BaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def handle(self, request: Request) -> Response:
        self.requests.append(request)
        return _respond(request)

    def close(self) -> None:
        return None


def detect_revision(context: ResponseContext[object]) -> Challenge | None:
    if context.response.status_code != 403:
        return None
    document = context.json.value
    if isinstance(document, dict) and isinstance(document.get("revision"), int):
        return Challenge(document["revision"])
    return None


class CookieGuard(Guard[Challenge]):
    """The acceptance scenario: one class, cookies pinned to the session, solved once."""

    scope = host("phase31.test")

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, response: ResponseContext[object]) -> Challenge | None:
        return detect_revision(response)

    async def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
        self.calls += 1
        return self.solution(cookies={"clearance": f"c{challenge.revision}-{self.calls}"})


def _cookies(handler: OriginHandler | SyncOriginHandler) -> list[str | None]:
    return [request.headers.get("Cookie") for request in handler.requests]


@pytest.mark.asyncio
async def test_one_guard_class_and_one_config_line_solve_once_per_session() -> None:
    guard = CookieGuard()
    handler = OriginHandler()
    async with AsyncClient(
        base_url=BASE_URL, handler=handler, config=ClientConfig(guards=[guard])
    ) as client:
        service = ProtectedApi(client)
        assert await service.protected() == {"ok": True}
        assert await service.protected() == {"ok": True}
        assert await service.protected() == {"ok": True}

    assert guard.calls == 1
    assert _cookies(handler) == [None, "clearance=c3-1", "clearance=c3-1", "clearance=c3-1"]


def test_guards_argument_lowers_like_with_protection_and_is_not_applied_twice() -> None:
    guard = CookieGuard()
    direct = ClientConfig(guards=[guard])
    fluent = ClientConfig().with_protection(guard)
    assert direct.guards == ()
    assert [policy.identity for policy in direct.bundle.challenge_policies] == ["CookieGuard"]
    assert len(direct.bundle.challenge_policies) == len(fluent.bundle.challenge_policies) == 1
    assert len(direct.bundle.solver_bindings) == 1
    # A copy of a lowered config must not lower the same guard a second time.
    assert len(ClientConfig(guards=[guard]).with_protection().bundle.challenge_policies) == 1
    with pytest.raises(ValueError, match="duplicate protection policy identity"):
        ClientConfig(guards=[guard, CookieGuard()])
    with pytest.raises(ProtectionConfigurationError):
        ClientConfig(guards=[object()])  # type: ignore[list-item]


def test_guard_defaults_lower_to_session_scoped_until_rejected() -> None:
    bundle = CookieGuard().to_bundle()
    (policy,) = bundle.challenge_policies
    assert policy.identity == "CookieGuard"
    assert policy.persistence.mode is ProtectionPersistenceMode.UNTIL_REJECTED
    assert policy.persistence.scope.scope is ProtectionStateScope.SESSION
    assert policy.replay.max_replays == 1
    assert policy.scope == host("phase31.test")
    assert Guard.scope == RequestScope()  # whole-client default scope
    assert isinstance(challenge_guard(
        detect=detect_revision,
        solver=lambda challenge, context: {"token": "x"},
        apply=solution_fields(cookies={"clearance": "token"}),
    ), _ChallengeGuard)


@pytest.mark.asyncio
async def test_guard_declared_headers_sync_solve_and_undeclared_destinations() -> None:
    class HeaderGuard(Guard[Challenge]):
        name = "phase31.header"
        headers = ("X-Token",)
        cache = "call"

        def detect(self, response: ResponseContext[object]) -> Challenge | None:
            return detect_revision(response)

        def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
            return self.solution(headers={"X-Token": f"t{challenge.revision}"})

    handler = OriginHandler()
    async with AsyncClient(
        base_url=BASE_URL, handler=handler, config=ClientConfig(guards=[HeaderGuard()])
    ) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}
    assert handler.requests[1].headers.get("X-Token") == "t3"
    assert handler.requests[1].headers.get("Cookie") is None

    with pytest.raises(ProtectionConfigurationError, match="undeclared header"):
        HeaderGuard().solution(headers={"X-Other": "1"})
    with pytest.raises(ProtectionConfigurationError, match="undeclared query"):
        HeaderGuard().solution(query={"q": "1"})
    with pytest.raises(ValueError):
        HeaderGuard().solution(expires_in=0)
    with pytest.raises(ValueError, match="either"):
        HeaderGuard().solution(expires_in=1, expires_at=datetime.now(UTC))
    with pytest.raises(ValueError, match="timezone"):
        HeaderGuard().solution(expires_at=datetime(2030, 1, 1))
    rendered = repr(HeaderGuard().solution(headers={"X-Token": "secret"}, cookies={"a": "s"}))
    assert "secret" not in rendered and "X-Token" in rendered and "'a'" in rendered


@pytest.mark.asyncio
async def test_cache_none_and_call_solve_again_on_the_next_call() -> None:
    for cache, expected_calls in (("none", 2), ("call", 2), ("session", 1)):
        calls = 0

        def solve(
            challenge: Challenge, context: SolveContext, label: str = cache
        ) -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"token": f"{label}-{calls}"}

        guard = challenge_guard(
            name=f"phase31.{cache}",
            detect=detect_revision,
            solver=solve,
            apply=solution_fields(cookies={"clearance": "token"}),
            cache=cache,  # type: ignore[arg-type]
        )
        handler = OriginHandler()
        async with AsyncClient(
            base_url=BASE_URL, handler=handler, config=ClientConfig(guards=[guard])
        ) as client:
            assert await ProtectedApi(client).protected() == {"ok": True}
            assert await ProtectedApi(client).protected() == {"ok": True}
        assert calls == expected_calls, cache


def test_challenge_guard_infers_name_from_the_detector() -> None:
    guard = challenge_guard(
        detect=detect_revision,
        solver=lambda challenge, context: {"token": "x"},
        apply=solution_fields(cookies={"clearance": "token"}),
    )
    (policy,) = guard.to_bundle().challenge_policies
    assert policy.identity == "detect_revision"
    assert policy.persistence.mode is ProtectionPersistenceMode.PER_MATCH

    class Detector:
        def detect(self, response: ResponseContext[object]) -> Challenge | None:
            return None

    (policy,) = challenge_guard(
        detect=Detector().detect,
        solver=lambda challenge, context: {"token": "x"},
        apply=solution_fields(cookies={"clearance": "token"}),
    ).to_bundle().challenge_policies
    assert policy.identity == "Detector"

    with pytest.raises(ProtectionConfigurationError, match="cannot be inferred"):
        challenge_guard(
            detect=lambda response: None,
            solver=lambda challenge, context: {"token": "x"},
            apply=solution_fields(cookies={"clearance": "token"}),
        )
    with pytest.raises(ProtectionConfigurationError, match="cache"):
        challenge_guard(
            name="x",
            detect=detect_revision,
            solver=lambda challenge, context: {"token": "x"},
            apply=solution_fields(cookies={"clearance": "token"}),
            cache="forever",  # type: ignore[arg-type]
        )
    with pytest.raises(ProtectionConfigurationError, match="solver"):
        challenge_guard(
            name="x",
            detect=detect_revision,
            solver=object(),  # type: ignore[arg-type]
            apply=solution_fields(cookies={"clearance": "token"}),
        )


@pytest.mark.asyncio
async def test_expires_in_bounds_session_cache() -> None:
    class ExpiringGuard(CookieGuard):
        async def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
            self.calls += 1
            return self.solution(cookies={"clearance": f"e{self.calls}"}, expires_in=0.05)

    guard = ExpiringGuard()
    handler = OriginHandler()
    async with AsyncClient(
        base_url=BASE_URL, handler=handler, config=ClientConfig(guards=[guard])
    ) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}
        assert await ProtectedApi(client).protected() == {"ok": True}
        assert guard.calls == 1
        await asyncio.sleep(0.08)
        assert await ProtectedApi(client).protected() == {"ok": True}
    assert guard.calls == 2
    assert _cookies(handler)[-1] == "clearance=e2"


@pytest.mark.asyncio
async def test_public_invalidation_by_guard_name_and_all() -> None:
    guard = CookieGuard()
    other = challenge_guard(
        name="phase31.other",
        scope=operation("phase31.none"),
        detect=detect_revision,
        solver=lambda challenge, context: {"token": "x"},
        apply=solution_fields(cookies={"other": "token"}),
        cache="session",
    )
    handler = OriginHandler()
    async with AsyncClient(
        base_url=BASE_URL, handler=handler, config=ClientConfig(guards=[guard, other])
    ) as client:
        service = ProtectedApi(client)
        assert await service.protected() == {"ok": True}
        assert client.invalidate_protection("phase31.other") == 0
        assert client.invalidate_protection(guard) == 1
        assert await service.protected() == {"ok": True}
        assert guard.calls == 2
        assert client.invalidate_protection("CookieGuard") == 1
        assert await service.protected() == {"ok": True}
        assert client.invalidate_protection() == 1
        assert client.invalidate_protection() == 0
        with pytest.raises(TypeError):
            client.invalidate_protection(object())  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            client.invalidate_protection("")
    assert guard.calls == 3


@pytest.mark.asyncio
async def test_sync_client_guard_and_invalidation() -> None:
    guard = CookieGuard()
    handler = SyncOriginHandler()

    def run() -> int:
        with Client(
            base_url=BASE_URL, handler=handler, config=ClientConfig(guards=[guard])
        ) as client:
            service = SyncProtectedApi(client)
            assert service.protected() == {"ok": True}
            assert service.protected() == {"ok": True}
            dropped = client.invalidate_protection(guard)
            assert service.protected() == {"ok": True}
            return dropped

    assert await asyncio.to_thread(run) == 1
    assert guard.calls == 2
    assert _cookies(handler) == [None, "clearance=c3-1", "clearance=c3-1", None, "clearance=c3-2"]


@pytest.mark.asyncio
async def test_session_cache_single_flights_concurrent_challenges() -> None:
    class SlowGuard(CookieGuard):
        async def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
            self.calls += 1
            await asyncio.sleep(0.02)
            return self.solution(cookies={"clearance": "shared"})

    guard = SlowGuard()
    handler = OriginHandler()
    async with AsyncClient(
        base_url=BASE_URL, handler=handler, config=ClientConfig(guards=[guard])
    ) as client:
        service = ProtectedApi(client)
        results = await asyncio.gather(*(service.protected() for _ in range(4)))
    assert results == [{"ok": True}] * 4
    assert guard.calls == 1
    assert _cookies(handler).count("clearance=shared") == 4


@pytest.mark.asyncio
async def test_guard_solve_errors_are_wrapped_and_redacted() -> None:
    class BrokenGuard(CookieGuard):
        async def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
            return self.solution(headers={"X-Undeclared": "secret-value"})

    async with AsyncClient(
        base_url=BASE_URL, handler=OriginHandler(), config=ClientConfig(guards=[BrokenGuard()])
    ) as client:
        with pytest.raises(ChallengeSolveError) as error:
            await ProtectedApi(client).protected()
    assert isinstance(error.value.__cause__, ProtectionConfigurationError)
    assert "secret-value" not in str(error.value)

    with pytest.raises(NotImplementedError):
        Guard().detect(None)  # type: ignore[arg-type]


def test_scope_helpers_live_in_core_and_presets_reexport_them() -> None:
    presets = pytest.importorskip("eazy_sdk_presets")
    assert presets.host is host
    assert presets.operation is operation
    assert host("a.test", "b.test").hosts == frozenset({"a.test", "b.test"})
    assert operation(ProtectedApi.protected, "x").operation_ids == frozenset(
        {"phase31.protected", "x"}
    )
    with pytest.raises(TypeError):
        host("")
    with pytest.raises(TypeError):
        operation(object())
