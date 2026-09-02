from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest
from eazy_sdk_presets import cloudflare, host
from zapros import AsyncBaseHandler, BaseHandler, Request, Response

import eazy_sdk.protection as protection
from eazy_sdk import AsyncApi, AsyncClient, Client, ClientConfig, SyncApi, api
from eazy_sdk.protection import (
    ChallengeApplicationError,
    ChallengeDetectionError,
    ChallengeSolveError,
    MalformedChallengeError,
    SolveContext,
    challenge_guard,
    safe_method,
    solution_fields,
)
from eazy_sdk.protection.advanced import MalformedSignal, SignalMatch, _inspect_signals
from eazy_sdk.response import Json, ResponseContext, Responses, Success


@dataclass(frozen=True, slots=True)
class Challenge:
    revision: int


@dataclass(frozen=True, slots=True)
class Clearance:
    first: str
    second: str

    def __repr__(self) -> str:
        return "Clearance(<redacted>)"


class ProtectedApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="phase29.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(self) -> dict[str, object]:
        raise NotImplementedError


class SyncProtectedApi(SyncApi):
    @api.get(
        "/protected",
        operation_id="phase29.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def protected(self) -> dict[str, object]:
        raise NotImplementedError


def detect(context: ResponseContext[object]) -> Challenge | None:
    if context.response.status_code != 403:
        return None
    parsed = context.json
    if parsed.error is not None or not isinstance(parsed.value, dict):
        raise MalformedChallengeError("invalid challenge document")
    revision = parsed.value.get("revision")
    if not isinstance(revision, int):
        raise MalformedChallengeError("missing challenge revision")
    return Challenge(revision)


class Solver:
    def __init__(self) -> None:
        self.calls = 0
        self.contexts: list[SolveContext] = []

    async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
        self.calls += 1
        self.contexts.append(context)
        return Clearance(f"first-{challenge.revision}", f"second-{challenge.revision}")


class ChallengeHandler(AsyncBaseHandler):
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[Request] = []

    async def ahandle(self, request: Request) -> Response:
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            return Response(
                403,
                [("Content-Type", "application/json")],
                content=b'{"revision":7}',
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


class SyncChallengeHandler(BaseHandler):
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[Request] = []

    def handle(self, request: Request) -> Response:
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            return Response(
                403,
                [("Content-Type", "application/json")],
                content=b'{"revision":7}',
                request=request,
            )
        return Response(
            200,
            [("Content-Type", "application/json")],
            content=b'{"ok":true}',
            request=request,
        )

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_custom_guard_lowers_to_existing_executor_and_applies_two_cookies() -> None:
    solver = Solver()
    guard = challenge_guard(
        name="phase29.custom",
        scope=host("phase29.test"),
        detect=detect,
        solver=solver,
        apply=solution_fields(
            cookies={"first": "first", "second": "second"},
        ),
        replay=safe_method(max_replays=1),
    )
    handler = ChallengeHandler()
    events: list[str] = []
    config = ClientConfig(observer=lambda phase, _value: events.append(phase)).with_protection(
        guard
    )

    async with AsyncClient(
        base_url="https://phase29.test",
        handler=handler,
        config=config,
    ) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}

    replay_cookie = handler.requests[1].headers.get("Cookie")
    assert replay_cookie is not None
    assert "first=first-7" in replay_cookie
    assert "second=second-7" in replay_cookie
    assert solver.calls == 1
    assert len(solver.contexts) == 1
    assert "network_identity" not in inspect.signature(SolveContext).parameters
    assert events.count("emit") == 2
    assert events.count("prepared") == 2


@pytest.mark.asyncio
async def test_custom_guard_sync_and_async_traces_are_equivalent() -> None:
    def configured(solver: Solver, events: list[str]) -> ClientConfig:
        return ClientConfig(
            observer=lambda phase, _value: events.append(phase)
        ).with_protection(
            challenge_guard(
                name="phase29.trace",
                scope=host("phase29.test"),
                detect=detect,
                solver=solver,
                apply=solution_fields(cookies={"first": "first"}),
            )
        )

    async_events: list[str] = []
    async_handler = ChallengeHandler()
    async with AsyncClient(
        base_url="https://phase29.test",
        handler=async_handler,
        config=configured(Solver(), async_events),
    ) as client:
        async_result = await ProtectedApi(client).protected()

    sync_events: list[str] = []
    sync_handler = SyncChallengeHandler()

    def run_sync() -> dict[str, object]:
        with Client(
            base_url="https://phase29.test",
            handler=sync_handler,
            config=configured(Solver(), sync_events),
        ) as client:
            return SyncProtectedApi(client).protected()

    sync_result = await asyncio.to_thread(run_sync)
    assert sync_result == async_result == {"ok": True}
    assert sync_events == async_events
    assert sync_handler.requests[1].headers.get("Cookie") == "first=first-7"


@pytest.mark.parametrize("implementation", ("remote", "api", "wasm", "browser"))
@pytest.mark.asyncio
async def test_solver_implementations_need_no_technology_flags(
    implementation: str,
) -> None:
    class TechnologyAgnosticSolver:
        async def solve(
            self,
            challenge: Challenge,
            context: SolveContext,
        ) -> Clearance:
            assert context.attempt == 1
            return Clearance(implementation, "shared")

    solver = TechnologyAgnosticSolver()
    handler = ChallengeHandler()
    config = ClientConfig().with_protection(
        challenge_guard(
            name=f"phase29.{implementation}",
            scope=host("phase29.test"),
            detect=detect,
            solver=solver,
            apply=solution_fields(cookies={"first": "first"}),
        )
    )

    async with AsyncClient(
        base_url="https://phase29.test",
        handler=handler,
        config=config,
    ) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}

    assert handler.requests[1].headers.get("Cookie") == f"first={implementation}"
    assert set(inspect.signature(solver.solve).parameters) == {"challenge", "context"}


def test_simple_detector_preserves_match_no_match_malformed_and_unexpected_error() -> None:
    guard = challenge_guard(
        name="phase29.detect",
        scope=host("phase29.test"),
        detect=detect,
        solver=Solver(),
        apply=solution_fields(cookies={"first": "first"}),
    )
    policy = guard.to_bundle().challenge_policies[0]
    scope = __import__("eazy_sdk._internal", fromlist=["ScopeContext"]).ScopeContext(
        "https",
        "phase29.test",
        "/protected",
        "GET",
        __import__("eazy_sdk._internal", fromlist=["OperationIdentity"]).OperationIdentity(
            "phase29.protected"
        ),
    )

    def context(status: int, body: bytes) -> ResponseContext[object]:
        from eazy_sdk.response import NormalizedResponse

        return ResponseContext(
            NormalizedResponse(
                status,
                "https://phase29.test/protected",
                "GET",
                {"Content-Type": "application/json"},
                body,
            )
        )

    assert isinstance(
        _inspect_signals((policy.signal,), context(403, b'{"revision":1}'), scope),
        SignalMatch,
    )
    assert _inspect_signals((policy.signal,), context(200, b'{"ok":true}'), scope) is None
    assert isinstance(
        _inspect_signals((policy.signal,), context(403, b'{}'), scope),
        MalformedSignal,
    )

    secret = "response-body-secret"

    def broken(_context: ResponseContext[object]) -> Challenge | None:
        raise RuntimeError(secret)

    broken_guard = challenge_guard(
        name="phase29.broken-detect",
        scope=host("phase29.test"),
        detect=broken,
        solver=Solver(),
        apply=solution_fields(cookies={"first": "first"}),
    )
    broken_policy = broken_guard.to_bundle().challenge_policies[0]
    with pytest.raises(ChallengeDetectionError) as captured:
        _inspect_signals((broken_policy.signal,), context(403, b'{}'), scope)
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


@pytest.mark.asyncio
async def test_solver_and_application_failures_have_redacted_stage_errors() -> None:
    solve_secret = "solver-secret"

    class BrokenSolver:
        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            raise RuntimeError(solve_secret)

    solve_handler = ChallengeHandler()
    solve_config = ClientConfig().with_protection(
        challenge_guard(
            name="phase29.solve",
            scope=host("phase29.test"),
            detect=detect,
            solver=BrokenSolver(),
            apply=solution_fields(cookies={"first": "first"}),
        )
    )
    async with AsyncClient(
        base_url="https://phase29.test",
        handler=solve_handler,
        config=solve_config,
    ) as client:
        with pytest.raises(ChallengeSolveError) as solve_error:
            await ProtectedApi(client).protected()
    assert isinstance(solve_error.value.__cause__, RuntimeError)
    assert solve_secret not in str(solve_error.value)
    assert solve_secret not in repr(solve_error.value)

    application_handler = ChallengeHandler()
    application_config = ClientConfig().with_protection(
        challenge_guard(
            name="phase29.application",
            scope=host("phase29.test"),
            detect=detect,
            solver=Solver(),
            apply=solution_fields(cookies={"missing": "absent"}),
        )
    )
    async with AsyncClient(
        base_url="https://phase29.test",
        handler=application_handler,
        config=application_config,
    ) as client:
        with pytest.raises(ChallengeApplicationError) as application_error:
            await ProtectedApi(client).protected()
    assert application_error.value.__cause__ is not None
    assert "first-7" not in str(application_error.value)
    assert application_handler.calls == 1


@pytest.mark.asyncio
async def test_correct_cloudflare_solver_has_session_owned_state_without_flags() -> None:
    class CloudflareSolver:
        def __init__(self) -> None:
            self.calls = 0

        async def solve(
            self,
            challenge: cloudflare.CloudflareChallenge,
            context: SolveContext,
        ) -> cloudflare.CloudflareClearance:
            self.calls += 1
            return cloudflare.CloudflareClearance(
                (cloudflare.SecretCookie("cf_clearance", f"token-{self.calls}"),),
            )

    class Handler(AsyncBaseHandler):
        def __init__(self) -> None:
            self.requests: list[Request] = []

        async def ahandle(self, request: Request) -> Response:
            self.requests.append(request)
            cookie = request.headers.get("Cookie")
            if cookie is None:
                return Response(
                    403,
                    [("Content-Type", "text/html"), ("cf-mitigated", "challenge")],
                    content=b"<html>managed challenge</html>",
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

    solver = CloudflareSolver()
    config = ClientConfig().with_protection(
        cloudflare.challenge_pages(scope=host("phase29.test"), solver=solver)
    )
    first = Handler()
    async with AsyncClient(
        base_url="https://phase29.test",
        handler=first,
        config=config,
    ) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}
        assert await ProtectedApi(client).protected() == {"ok": True}
        runtime = client._runtime
        assert runtime._protection_state
    assert runtime._protection_state == {}
    assert solver.calls == 1
    assert len(first.requests) == 3

    second = Handler()
    async with AsyncClient(
        base_url="https://phase29.test",
        handler=second,
        config=config,
    ) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}
    assert solver.calls == 2
    assert len(second.requests) == 2


@pytest.mark.asyncio
async def test_initial_send_and_replay_use_one_borrowed_native_session() -> None:
    from eazy_sdk.handlers.httpx import AsyncHttpxHandler

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                403,
                headers={
                    "Content-Type": "text/html",
                    "cf-mitigated": "challenge",
                },
                content=b"<html>managed challenge</html>",
            )
        assert "cf_clearance=shared-session" in request.headers["Cookie"]
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b'{"ok":true}',
        )

    class Solver:
        async def solve(
            self,
            challenge: cloudflare.CloudflareChallenge,
            context: SolveContext,
        ) -> cloudflare.CloudflareClearance:
            return cloudflare.CloudflareClearance(
                (cloudflare.SecretCookie("cf_clearance", "shared-session"),),
            )

    native = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    handler = AsyncHttpxHandler(native, owns_client=False)
    config = ClientConfig().with_protection(
        cloudflare.challenge_pages(scope=host("phase29.test"), solver=Solver())
    )
    async with AsyncClient(
        base_url="https://phase29.test",
        handler=handler,
        config=config,
    ) as client:
        assert client._runtime.protection_session_owner is handler
        assert await ProtectedApi(client).protected() == {"ok": True}

    assert len(requests) == 2
    assert handler.client is native
    assert not native.is_closed
    await native.aclose()


def test_public_surface_and_constructors_exclude_removed_runtime_metadata() -> None:
    from eazy_sdk.handlers.curl_cffi import (
        AsyncCurlCffiZaprosHandler,
        CurlCffiZaprosHandler,
    )
    from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler
    from eazy_sdk.handlers.requests import RequestsHandler

    forbidden = {
        "BodyAccess",
        "CapableChallengeSolver",
        "NetworkIdentity",
        "NetworkIdentityContext",
        "NetworkIdentityExpectation",
        "NetworkIdentityProvider",
        "ProtectionBundle",
        "ProtectionCapabilities",
        "ResponseSignal",
        "SolverRequirement",
    }
    assert not forbidden.intersection(protection.__all__)
    assert all(not hasattr(protection, name) for name in forbidden)
    assert protection.__all__ == sorted(protection.__all__)

    for constructor in (
        ClientConfig,
        RequestsHandler,
        HttpxHandler,
        AsyncHttpxHandler,
        CurlCffiZaprosHandler,
        AsyncCurlCffiZaprosHandler,
    ):
        assert "network_identity" not in inspect.signature(constructor).parameters

    guard = challenge_guard(
        name="phase29.immutable",
        scope=host("phase29.test"),
        detect=detect,
        solver=Solver(),
        apply=solution_fields(cookies={"first": "first"}),
    )
    detector = cast(Any, guard.to_bundle().challenge_policies[0].signal.parser)
    assert "PreparedRequest" not in str(inspect.signature(detector.callback))
