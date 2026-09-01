from __future__ import annotations

import asyncio
import inspect
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from eazy_sdk import AsyncApi, PlanError, api
from eazy_sdk._internal import RequestScope
from eazy_sdk.auth import BearerScheme
from eazy_sdk.auth.core import AuthProviderIdentity, AuthProviders, StaticAuthProvider
from eazy_sdk.clients import CallOptions, ClientConfig
from eazy_sdk.clients.async_client import _AsyncClientCore
from eazy_sdk.clients.executor import ExecutionRuntime
from eazy_sdk.ext import ParsedValue
from eazy_sdk.handlers import EmitOptions, HandlerProfile, TransportFailure
from eazy_sdk.protection import (
    ChallengeSolverBindings,
    ProtectionPersistenceMode,
    ResponseSignal,
    SolveContext,
    SolverRequirement,
    before_call_policy,
    bind_challenge_solver,
    challenge_policy,
    network_identity,
    per_attempt,
    per_call,
    per_match,
    private_bindings,
    private_cookie,
    private_header,
    private_query,
    safe_method,
    session_identity,
    until_expiry,
    until_rejected,
)
from eazy_sdk.request.prepared import HttpProtocol, PreparedRequest
from eazy_sdk.response import Headers, Json, NormalizedResponse, Responses, Success, callable_parser


@dataclass(frozen=True, slots=True)
class Challenge:
    revision: int


@dataclass(frozen=True, slots=True)
class Clearance:
    pr_fp: str
    wasm: str
    trace: str

    def __repr__(self) -> str:
        return "Clearance(<redacted>)"


KAD_SOLVER = SolverRequirement[Challenge, Clearance]("kad.wasm")
BEFORE_SOLVER = SolverRequirement[str, str]("before.token")
SCOPE = RequestScope(operation_ids=frozenset({"protected"}))


def _parse_challenge(context: Any) -> ParsedValue[Challenge]:
    value = cast(dict[str, object], context.json.value)
    return ParsedValue(Challenge(cast(int, value["revision"])))


KAD_SIGNAL = ResponseSignal(
    "kad.challenge",
    SCOPE,
    Challenge,
    callable_parser(Challenge, _parse_challenge),
    prefilter=lambda context: context.response.status_code == 403,
)


def _policy(*, persistence: Any | None = None, revision: int = 7) -> Any:
    return challenge_policy(
        identity="kad.wasm",
        revision=revision,
        scope=SCOPE,
        signal=KAD_SIGNAL,
        solver=KAD_SOLVER,
        apply=private_bindings(
            private_cookie("pr_fp", field="pr_fp"),
            private_cookie("wasm", field="wasm"),
            private_header("X-KAD-Trace", field="trace"),
            private_query("kad_trace", field="trace"),
        ),
        persistence=persistence or until_rejected(scope=network_identity()),
        replay=safe_method(max_replays=2),
        challenge_identity=lambda challenge: challenge.revision,
    )


class ProtectedApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(self) -> dict[str, object]:
        raise NotImplementedError


class BeforeProtectedApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(
        self,
        *,
        options: CallOptions | None = None,
    ) -> dict[str, object]:
        raise NotImplementedError


def _response(request: PreparedRequest, status: int, body: bytes) -> NormalizedResponse[object]:
    return NormalizedResponse(
        status,
        request.url,
        "GET",
        Headers((('content-type', 'application/json'),)),
        body,
    )


def _header(request: PreparedRequest, name: bytes) -> bytes | None:
    return next(
        (item.value for item in request.headers if item.name.lower() == name.lower()),
        None,
    )


def _runtime(
    emit: Any,
    solver: Any,
    *,
    policy: Any | None = None,
    auth: AuthProviders | None = None,
) -> ExecutionRuntime:
    selected = policy or _policy()
    return ExecutionRuntime(
        HandlerProfile(frozenset({HttpProtocol.HTTP_1_1})),
        emit,
        "https://kad.test",
        auth=auth or AuthProviders(),
        challenge_policies=(selected,),
        challenge_solvers=ChallengeSolverBindings(
            bind_challenge_solver(selected.solver, solver)
        ),
        network_identity="proxy-a|ua-a",
    )


@pytest.mark.asyncio
async def test_compound_private_bindings_are_atomic_absent_from_signature_and_reused() -> None:
    class Solver:
        calls = 0

        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            self.calls += 1
            return Clearance("fp-1", "wasm-1", "trace-1")

    solver = Solver()
    requests: list[PreparedRequest] = []

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        requests.append(request)
        cookies = _header(request, b"cookie")
        if cookies is None:
            return _response(request, 403, b'{"revision":1}')
        assert b"pr_fp=fp-1" in cookies and b"wasm=wasm-1" in cookies
        assert _header(request, b"x-kad-trace") == b"trace-1"
        assert "kad_trace=trace-1" in request.url
        return _response(request, 200, b'{"ok":true}')

    runtime = _runtime(emit, solver)
    api_client = ProtectedApi(_AsyncClientCore(runtime))
    assert await api_client.protected() == {"ok": True}
    assert await api_client.protected() == {"ok": True}

    signature = inspect.signature(api_client.protected)
    assert "pr_fp" not in signature.parameters
    assert "wasm" not in signature.parameters
    assert solver.calls == 1
    assert len(requests) == 3
    assert "fp-1" not in repr(runtime._protection_state)


@pytest.mark.asyncio
async def test_repeated_challenge_invalidates_revision_and_network_change_does_not_reuse() -> None:
    class Solver:
        calls = 0

        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            self.calls += 1
            return Clearance(f"fp-{self.calls}", f"wasm-{self.calls}", f"trace-{self.calls}")

    solver = Solver()
    phase = 0

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal phase
        cookies = (_header(request, b"cookie") or b"").decode()
        if "pr_fp=fp-1" in cookies and phase == 0:
            phase = 1
            return _response(request, 200, b'{"ok":true}')
        if "pr_fp=fp-2" in cookies:
            return _response(request, 200, b'{"ok":true}')
        if "pr_fp=fp-3" in cookies:
            return _response(request, 200, b'{"ok":true}')
        return _response(request, 403, b'{"revision":1}')

    runtime = _runtime(emit, solver)
    client = ProtectedApi(_AsyncClientCore(runtime))
    assert await client.protected() == {"ok": True}
    # Force the origin to reject the cached first clearance.
    assert await client.protected() == {"ok": True}
    assert solver.calls == 2

    runtime.network_identity = "proxy-b|ua-b"
    assert await client.protected() == {"ok": True}
    assert solver.calls == 3


@pytest.mark.asyncio
async def test_session_scope_and_policy_revision_partition_managed_state() -> None:
    class Solver:
        calls = 0

        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            self.calls += 1
            return Clearance(f"fp-{self.calls}", "wasm", "trace")

    solver = Solver()
    accepted = {"fp-1", "fp-2", "fp-3"}

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        cookies = (_header(request, b"cookie") or b"").decode()
        if any(f"pr_fp={value}" in cookies for value in accepted):
            return _response(request, 200, b'{"ok":true}')
        return _response(request, 403, b'{"revision":1}')

    policy = _policy(persistence=until_rejected(scope=session_identity()))
    runtime = _runtime(emit, solver, policy=policy)
    runtime.protection_session_identity = "session-a"
    client = ProtectedApi(_AsyncClientCore(runtime))
    await client.protected()
    await client.protected()
    assert solver.calls == 1

    runtime.protection_session_identity = "session-b"
    await client.protected()
    assert solver.calls == 2

    runtime.challenge_policies = (_policy(persistence=policy.persistence, revision=8),)
    await client.protected()
    assert solver.calls == 3


@pytest.mark.asyncio
async def test_concurrent_matches_share_one_solve() -> None:
    both_challenged = asyncio.Event()
    initial = 0

    class Solver:
        calls = 0

        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            self.calls += 1
            await asyncio.sleep(0.02)
            return Clearance("fp", "wasm", "trace")

    solver = Solver()

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal initial
        if _header(request, b"cookie") is None:
            initial += 1
            if initial == 2:
                both_challenged.set()
            await both_challenged.wait()
            return _response(request, 403, b'{"revision":1}')
        return _response(request, 200, b'{"ok":true}')

    client = ProtectedApi(_AsyncClientCore(_runtime(emit, solver)))
    results = await asyncio.gather(client.protected(), client.protected())
    assert results[0] == {"ok": True}
    assert results[1] == {"ok": True}
    assert solver.calls == 1


@pytest.mark.asyncio
async def test_cancelled_solve_does_not_publish_partial_state() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class Solver:
        calls = 0

        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            self.calls += 1
            if self.calls == 1:
                started.set()
                await release.wait()
            return Clearance("fp", "wasm", "trace")

    solver = Solver()

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        if _header(request, b"cookie") is None:
            return _response(request, 403, b'{"revision":1}')
        return _response(request, 200, b'{"ok":true}')

    runtime = _runtime(emit, solver)
    client = ProtectedApi(_AsyncClientCore(runtime))
    task = asyncio.create_task(client.protected())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime._protection_state == {}
    assert await client.protected() == {"ok": True}
    assert solver.calls == 2


@pytest.mark.asyncio
async def test_failed_compound_binding_commits_neither_replay_nor_cache() -> None:
    class Solver:
        calls = 0

        async def solve(self, challenge: Challenge, context: SolveContext) -> object:
            self.calls += 1
            if self.calls == 1:
                return {"pr_fp": "partial"}
            return {"pr_fp": "fp", "wasm": "wasm", "trace": "trace"}

    solver = Solver()
    emits = 0

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal emits
        emits += 1
        if _header(request, b"cookie") is None:
            return _response(request, 403, b'{"revision":1}')
        return _response(request, 200, b'{"ok":true}')

    runtime = _runtime(emit, solver)
    client = ProtectedApi(_AsyncClientCore(runtime))
    with pytest.raises(PlanError, match="solution field is missing: wasm"):
        await client.protected()
    assert emits == 1
    assert runtime._protection_state == {}
    assert await client.protected() == {"ok": True}
    assert solver.calls == 2


@pytest.mark.asyncio
async def test_auth_and_protection_apply_independently_to_the_same_attempts() -> None:
    bearer = BearerScheme()
    providers = AuthProviders()
    providers.register(
        bearer,
        StaticAuthProvider(bearer, "auth-secret", AuthProviderIdentity("phase24")),
    )

    class SecuredApi(AsyncApi):
        @api.get(
            "/protected",
            operation_id="protected",
            security=bearer,
            responses=Responses(success=(Success(200, Json(dict)),)),
        )
        async def protected(self) -> dict[str, object]:
            raise NotImplementedError

    class Solver:
        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            return Clearance("fp", "wasm", "trace")

    calls = 0

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal calls
        calls += 1
        assert _header(request, b"authorization") == b"Bearer auth-secret"
        if calls == 1:
            return _response(request, 403, b'{"revision":1}')
        assert b"pr_fp=fp" in cast(bytes, _header(request, b"cookie"))
        return _response(request, 200, b'{"ok":true}')

    runtime = _runtime(emit, Solver(), auth=providers)
    assert await SecuredApi(_AsyncClientCore(runtime)).protected() == {"ok": True}


@pytest.mark.parametrize(
    ("persistence", "expected_solves"),
    [(per_call(), 1), (per_attempt(), 2)],
)
@pytest.mark.asyncio
async def test_before_call_per_call_and_per_attempt_lifetimes(
    persistence: Any,
    expected_solves: int,
) -> None:
    class Solver:
        calls = 0

        async def solve(self, challenge: str, context: SolveContext) -> str:
            self.calls += 1
            return f"token-{self.calls}"

    solver = Solver()
    policy = before_call_policy(
        identity=f"before.{persistence.mode.value}",
        scope=SCOPE,
        challenge="static",
        solver=BEFORE_SOLVER,
        apply=private_bindings(private_header("X-Before-Token")),
        persistence=persistence,
    )
    sends = 0

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal sends
        sends += 1
        expected = (
            b"token-1"
            if persistence.mode is ProtectionPersistenceMode.PER_CALL
            else f"token-{sends}".encode()
        )
        assert _header(request, b"x-before-token") == expected
        if sends == 1:
            raise TransportFailure("phase24", "emit", 1, OSError("retry"))
        return _response(request, 200, b'{"ok":true}')

    runtime = ExecutionRuntime(
        HandlerProfile(frozenset({HttpProtocol.HTTP_1_1})),
        emit,
        "https://kad.test",
        before_call_policies=(policy,),
        challenge_solvers=ChallengeSolverBindings(
            bind_challenge_solver(BEFORE_SOLVER, solver)
        ),
    )
    client = BeforeProtectedApi(_AsyncClientCore(runtime))
    result = await client.protected(
        options=CallOptions(max_attempts=2, transport_retries=1)
    )
    assert result == {"ok": True}
    assert solver.calls == expected_solves


def test_persistence_modes_and_malformed_config_contract() -> None:
    assert per_match().mode is ProtectionPersistenceMode.PER_MATCH
    assert per_call().mode is ProtectionPersistenceMode.PER_CALL
    assert per_attempt().mode is ProtectionPersistenceMode.PER_ATTEMPT
    assert until_expiry().mode is ProtectionPersistenceMode.UNTIL_EXPIRY
    assert until_rejected().mode is ProtectionPersistenceMode.UNTIL_REJECTED

    with pytest.raises(TypeError, match="malformed policy"):
        ClientConfig(challenge_policies=(cast(Any, object()),))


def test_old_ambiguous_config_fields_and_executor_discovery_are_absent() -> None:
    config_parameters = inspect.signature(ClientConfig).parameters
    for old_name in ("signals", "protections", "solvers", "protection_solvers"):
        assert old_name not in config_parameters
    for name in (
        "operation_protections",
        "before_call_policies",
        "challenge_policies",
        "operation_protection_solvers",
        "challenge_solvers",
    ):
        assert name in config_parameters

    source = inspect.getsource(sys.modules[ExecutionRuntime.__module__])
    assert 'getattr(protection, "before"' not in source
    assert 'getattr(protection, "reactions"' not in source
    assert 'getattr(protection, "signals"' not in source
    assert 'getattr(protection, "solver_requirement"' not in source


def test_malformed_policy_is_rejected_by_strict_typing(tmp_path: Path) -> None:
    source = tmp_path / "invalid_policy.py"
    source.write_text(
        """
from eazy_sdk.clients import ClientConfig
from eazy_sdk_presets import ProtectionCapabilities

ClientConfig(challenge_policies=(object(),))
ClientConfig(challenge_polices=())
ProtectionCapabilities(cookie_jarr=True)
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(source)],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "challenge_policies" in result.stdout
    assert "challenge_polices" in result.stdout
    assert "cookie_jarr" in result.stdout
