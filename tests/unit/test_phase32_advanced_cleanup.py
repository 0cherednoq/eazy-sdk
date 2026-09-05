"""Phase 32: advanced cleanup (no duplicates, typed policies, malformed/ambiguous, budgets)."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

import pytest
from zapros import AsyncBaseHandler, BaseHandler, Request, Response

import eazy_sdk.protection.advanced as advanced
from eazy_sdk import AsyncApi, AsyncClient, Client, ClientConfig, SyncApi, api
from eazy_sdk.clients.executor import _ProtectionLockRegistry
from eazy_sdk.protection import (
    Guard,
    GuardSolution,
    MalformedChallengeError,
    ProtectionConfigurationError,
    ReplayDeniedError,
    SolutionFields,
    SolveContext,
    challenge_guard,
    host,
    solution_cookie_set,
    solution_fields,
)
from eazy_sdk.protection.advanced import (
    AmbiguousChallengeError,
    BeforeCallPolicy,
    ChallengeParseError,
    ChallengePolicy,
    PrivateBindings,
    PrivateCookieSetBinding,
    ProtectionBundle,
    SolverBinding,
    SolverBindings,
    SolverRequirement,
    _detector_model,
)
from eazy_sdk.response import Json, ResponseContext, Responses, Success

BASE_URL = "https://phase32.test"


@dataclass(frozen=True, slots=True)
class Challenge:
    revision: int


class ProtectedApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="phase32.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(self) -> dict[str, object]:
        raise NotImplementedError


class SyncProtectedApi(SyncApi):
    @api.get(
        "/protected",
        operation_id="phase32.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def protected(self) -> dict[str, object]:
        raise NotImplementedError


def _challenge(request: Request, body: bytes = b'{"revision":1}') -> Response:
    return Response(
        403, [("Content-Type", "application/json")], content=body, request=request
    )


def _ok(request: Request) -> Response:
    return Response(
        200, [("Content-Type", "application/json")], content=b'{"ok":true}', request=request
    )


class Handler(AsyncBaseHandler):
    def __init__(self, script: list[bytes | None]) -> None:
        self.script = list(script)
        self.requests: list[Request] = []

    async def ahandle(self, request: Request) -> Response:
        self.requests.append(request)
        body = self.script.pop(0) if self.script else None
        return _ok(request) if body is None else _challenge(request, body)

    async def aclose(self) -> None:
        return None


def detect_revision(context: ResponseContext[object]) -> Challenge | None:
    if context.response.status_code != 403:
        return None
    document = context.json.value
    if not isinstance(document, dict):
        raise MalformedChallengeError("challenge is not a JSON object")
    revision = document.get("revision")
    if not isinstance(revision, int):
        raise MalformedChallengeError("challenge revision is missing")
    return Challenge(revision)


def _solver(challenge: Challenge, context: SolveContext) -> dict[str, str]:
    return {"token": f"t{challenge.revision}"}


def test_duplicate_names_are_gone_and_single_definitions_remain() -> None:
    for removed in (
        "ProtectionSolver",
        "ProtectionRequirement",
        "ChallengeSolverBinding",
        "ChallengeSolverBindings",
        "bind_challenge_solver",
        "SolutionCookieSet",
        "ChallengePolicySpec",
        "BeforeCallPolicySpec",
    ):
        assert not hasattr(advanced, removed), removed
        assert removed not in advanced.__all__
    assert isinstance(solution_cookie_set(), PrivateCookieSetBinding)
    fields: SolutionFields[object] = solution_fields(
        cookies={"a": "token"}, cookie_set=solution_cookie_set()
    )
    assert isinstance(fields.bindings, PrivateBindings)
    assert not hasattr(fields, "_bindings")
    with pytest.raises(ProtectionConfigurationError, match="cookie_set"):
        solution_fields(cookie_set=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not be empty"):
        SolverRequirement[Challenge, object]("")
    requirement = SolverRequirement[Challenge, dict[str, str]]("phase32.req")
    registry = SolverBindings(SolverBinding(requirement, advanced._CallableSolver(_solver)))
    assert registry.get(requirement) is not None
    with pytest.raises(ValueError, match="only once"):
        SolverBindings(
            SolverBinding(requirement, advanced._CallableSolver(_solver)),
            SolverBinding(requirement, advanced._CallableSolver(_solver)),
        )
    assert ProtectionBundle.__dataclass_fields__.keys() == {
        "operation_protections",
        "before_call_policies",
        "challenge_policies",
        "solver_bindings",
    }


def test_policies_are_validating_dataclasses_not_protocols() -> None:
    guard = challenge_guard(
        name="phase32.guard",
        detect=detect_revision,
        solver=_solver,
        apply=solution_fields(cookies={"clearance": "token"}),
    )
    (policy,) = guard.to_bundle().challenge_policies
    assert isinstance(policy, ChallengePolicy)
    assert policy.signal.model is Challenge
    assert not hasattr(ChallengePolicy, "_is_runtime_protocol")
    assert advanced.challenge_policy(
        scope=policy.scope,
        signal=policy.signal,
        solver=policy.solver,
        apply=policy.apply,
        persistence=policy.persistence,
        replay=policy.replay,
    ).identity == "phase32.guard"
    with pytest.raises(ValueError, match="identity"):
        ChallengePolicy(
            identity="",
            revision=1,
            scope=policy.scope,
            signal=policy.signal,
            solver=policy.solver,
            apply=policy.apply,
            persistence=policy.persistence,
            replay=policy.replay,
        )
    with pytest.raises(TypeError, match="apply"):
        ChallengePolicy(
            identity="x",
            revision=1,
            scope=policy.scope,
            signal=policy.signal,
            solver=policy.solver,
            apply=object(),  # type: ignore[arg-type]
            persistence=policy.persistence,
            replay=policy.replay,
        )
    with pytest.raises(ValueError, match="exactly one"):
        BeforeCallPolicy(
            identity="x",
            revision=1,
            scope=policy.scope,
            acquire=None,
            challenge=None,
            solver=None,
            apply=policy.apply,
            persistence=policy.persistence,
        )
    with pytest.raises(TypeError, match="ChallengePolicy"):
        ProtectionBundle(challenge_policies=(object(),))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="malformed policy"):
        ClientConfig(
            protection=ProtectionBundle(
                challenge_policies=(object(),),  # type: ignore[arg-type]
            ),
        )

    presets = pytest.importorskip("eazy_sdk_presets")
    preset = presets.cloudflare.challenge_pages(scope=host("phase32.test"), solver=None)
    (preset_policy,) = preset.to_bundle().challenge_policies
    assert isinstance(preset_policy, ChallengePolicy)


def test_detector_model_is_inferred_from_the_return_annotation() -> None:
    assert _detector_model(detect_revision) is Challenge

    def untyped(context):  # type: ignore[no-untyped-def]
        return None

    assert _detector_model(untyped) is object
    assert _detector_model(lambda context: None) is object


@pytest.mark.asyncio
async def test_malformed_challenge_is_a_dedicated_error_with_policy_and_cause() -> None:
    handler = Handler([b'{"revision":"seven"}'])
    config = ClientConfig(
        guards=[
            challenge_guard(
                name="phase32.malformed",
                detect=detect_revision,
                solver=_solver,
                apply=solution_fields(cookies={"clearance": "token"}),
            )
        ]
    )
    async with AsyncClient(base_url=BASE_URL, handler=handler, config=config) as client:
        with pytest.raises(ChallengeParseError) as error:
            await ProtectedApi(client).protected()
    assert error.value.policy == "phase32.malformed"
    assert error.value.attempt == 1
    assert isinstance(error.value.__cause__, MalformedChallengeError)
    assert "seven" not in str(error.value)
    assert len(handler.requests) == 1


@pytest.mark.asyncio
async def test_two_definitive_policies_matching_one_response_is_a_configuration_error() -> None:
    handler = Handler([b'{"revision":1}'])
    config = ClientConfig(
        guards=[
            challenge_guard(
                name="phase32.first",
                detect=detect_revision,
                solver=_solver,
                apply=solution_fields(cookies={"first": "token"}),
            ),
            challenge_guard(
                name="phase32.second",
                detect=detect_revision,
                solver=_solver,
                apply=solution_fields(cookies={"second": "token"}),
            ),
        ]
    )
    async with AsyncClient(base_url=BASE_URL, handler=handler, config=config) as client:
        with pytest.raises(AmbiguousChallengeError) as error:
            await ProtectedApi(client).protected()
    assert error.value.policies == ("phase32.first", "phase32.second")
    assert isinstance(error.value, ProtectionConfigurationError)
    assert "phase32.first" in str(error.value) and "phase32.second" in str(error.value)


@pytest.mark.asyncio
async def test_replay_budget_is_per_policy_not_shared() -> None:
    # Policy A has one replay; policy B's budget must not let A replay a second time.
    def detect_a(context: ResponseContext[object]) -> Challenge | None:
        value = context.json.value if context.response.status_code == 403 else None
        return Challenge(1) if isinstance(value, dict) and value.get("kind") == "a" else None

    def detect_b(context: ResponseContext[object]) -> Challenge | None:
        value = context.json.value if context.response.status_code == 403 else None
        return Challenge(2) if isinstance(value, dict) and value.get("kind") == "b" else None

    handler = Handler([b'{"kind":"a"}', b'{"kind":"a"}'])
    config = ClientConfig(
        guards=[
            challenge_guard(
                name="phase32.a",
                detect=detect_a,
                solver=_solver,
                apply=solution_fields(cookies={"a": "token"}),
                cache="call",
            ),
            challenge_guard(
                name="phase32.b",
                detect=detect_b,
                solver=_solver,
                apply=solution_fields(cookies={"b": "token"}),
                cache="call",
            ),
        ]
    )
    async with AsyncClient(base_url=BASE_URL, handler=handler, config=config) as client:
        with pytest.raises(ReplayDeniedError, match="budget of this policy"):
            await ProtectedApi(client).protected()
    assert len(handler.requests) == 2

    handler = Handler([b'{"kind":"a"}', b'{"kind":"b"}'])
    async with AsyncClient(base_url=BASE_URL, handler=handler, config=config) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}
    assert len(handler.requests) == 3
    assert handler.requests[2].headers.get("Cookie") == "a=t1; b=t2"


@pytest.mark.asyncio
async def test_single_flight_lock_survives_multiple_event_loops_and_threads() -> None:
    registry = _ProtectionLockRegistry()
    order: list[str] = []

    async def hold(name: str, delay: float) -> None:
        async with registry.hold(("k",)):
            order.append(f"{name}:in")
            await asyncio.sleep(delay)
            order.append(f"{name}:out")

    def run(name: str, delay: float) -> None:
        asyncio.run(hold(name, delay))

    threads = [threading.Thread(target=run, args=(f"t{i}", 0.02)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(registry) == 0
    assert order[1::2] == [item.replace(":in", ":out") for item in order[0::2]]

    class SessionGuard(Guard[Challenge]):
        scope = host("phase32.test")

        def __init__(self) -> None:
            self.calls = 0

        def detect(self, response: ResponseContext[object]) -> Challenge | None:
            return detect_revision(response)

        def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
            self.calls += 1
            return self.solution(cookies={"clearance": "shared"})

    class SyncHandler(BaseHandler):
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.requests: list[Request] = []

        def handle(self, request: Request) -> Response:
            with self.lock:
                self.requests.append(request)
            if "clearance=" in (request.headers.get("Cookie") or ""):
                return _ok(request)
            return _challenge(request)

        def close(self) -> None:
            return None

    guard = SessionGuard()
    handler = SyncHandler()
    results: list[object] = []

    def worker(client: Client) -> None:
        results.append(SyncProtectedApi(client).protected())

    def run_sync() -> None:
        with Client(
            base_url=BASE_URL, handler=handler, config=ClientConfig(guards=[guard])
        ) as client:
            workers = [threading.Thread(target=worker, args=(client,)) for _ in range(4)]
            for thread in workers:
                thread.start()
            for thread in workers:
                thread.join()

    await asyncio.to_thread(run_sync)
    assert results == [{"ok": True}] * 4
    assert guard.calls == 1
