"""Vendor-neutral signals, reactions, before-call flows and replay safety."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, cast

from eazy_sdk._internal.errors import GraphError, PlanError
from eazy_sdk._internal.http_compiler import CompiledContract
from eazy_sdk._internal.http_plan import RequestScope, ScopeContext
from eazy_sdk._internal.kernel import OperationIdentity, OperationValues, Set, ValuePatch
from eazy_sdk.models import default_model_adapters
from eazy_sdk.request.prepared import BufferedBody, ReplayableBodyStream
from eazy_sdk.response import ResponseContext
from eazy_sdk.response.cases import (
    Error,
    ErrorOutcome,
    Malformed,
    NoMatch,
    ParsedValue,
    ResponseParser,
)


@dataclass(frozen=True, slots=True)
class NetworkIdentity:
    proxy: str | None = None
    user_agent: str | None = None
    address_family: str | None = None


@dataclass(frozen=True, slots=True)
class SolveContext:
    operation: OperationIdentity
    response: ResponseContext[object] | None
    attempt: int
    deadline: datetime | None = None
    network_identity: NetworkIdentity | None = None


class ChallengeSolver[TChallenge, TSolution](Protocol):
    async def solve(self, challenge: TChallenge, context: SolveContext) -> TSolution: ...


@dataclass(frozen=True, slots=True, eq=False)
class ProtectionRequirement[TResult]:
    """Typed identity of a mandatory operation protection result."""

    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("protection requirement name must not be empty")


@dataclass(frozen=True, slots=True)
class FromProtection:
    """Populate one exact wire-model field from a mandatory protection result."""

    requirement: ProtectionRequirement[Any]
    field: str

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("protection result field must not be empty")


class ProtectionSolver[TChallenge, TResult](Protocol):
    async def solve(self, challenge: TChallenge, context: SolveContext) -> TResult: ...


@dataclass(frozen=True, slots=True)
class SolverBinding[TChallenge, TResult]:
    requirement: ProtectionRequirement[TResult]
    solver: ProtectionSolver[TChallenge, TResult]


def bind_solver[TChallenge, TResult](
    requirement: ProtectionRequirement[TResult],
    solver: ProtectionSolver[TChallenge, TResult],
) -> SolverBinding[TChallenge, TResult]:
    return SolverBinding(requirement, solver)


@dataclass(frozen=True, slots=True)
class SolverBindings:
    bindings: tuple[SolverBinding[Any, Any], ...] = ()

    def __init__(self, *bindings: SolverBinding[Any, Any]) -> None:
        identities = [id(binding.requirement) for binding in bindings]
        if len(identities) != len(set(identities)):
            raise ValueError("a protection solver requirement can be bound only once")
        object.__setattr__(self, "bindings", bindings)

    def get[TResult](
        self,
        requirement: ProtectionRequirement[TResult],
    ) -> ProtectionSolver[Any, TResult] | None:
        for binding in self.bindings:
            if binding.requirement is requirement:
                return cast(ProtectionSolver[Any, TResult], binding.solver)
        return None


@dataclass(frozen=True, slots=True)
class ProtectionFlow[TResult]:
    """Bounded acquire -> optional solve -> optional verify flow."""

    requirement: ProtectionRequirement[TResult]
    acquire: object
    solve: bool = False
    verify: object | None = None


def protection_flow[TResult](
    requirement: ProtectionRequirement[TResult],
    *,
    acquire: object,
    solve: bool = False,
    verify: object | None = None,
) -> ProtectionFlow[TResult]:
    return ProtectionFlow(requirement, acquire, solve, verify)


@dataclass(frozen=True, slots=True, eq=False)
class SolverRequirement[TChallenge, TSolution]:
    name: str


class SolverRegistry:
    def __init__(self) -> None:
        self._values: dict[int, ChallengeSolver[Any, Any]] = {}

    def register[TChallenge, TSolution](
        self,
        requirement: SolverRequirement[TChallenge, TSolution],
        solver: ChallengeSolver[TChallenge, TSolution],
    ) -> None:
        self._values[id(requirement)] = solver

    def get[TChallenge, TSolution](
        self, requirement: SolverRequirement[TChallenge, TSolution]
    ) -> ChallengeSolver[TChallenge, TSolution] | None:
        return cast(
            ChallengeSolver[TChallenge, TSolution] | None,
            self._values.get(id(requirement)),
        )


class SignalInterception(Enum):
    DEFINITIVE = "definitive"
    ADVISORY = "advisory"


@dataclass(frozen=True, slots=True, eq=False)
class ResponseSignal[T]:
    name: str
    scope: RequestScope
    model: type[T]
    parser: ResponseParser
    prefilter: Any | None = None
    priority: int = 0
    interception: SignalInterception = SignalInterception.DEFINITIVE


@dataclass(frozen=True, slots=True)
class SignalMatch[T]:
    signal: ResponseSignal[T]
    value: T
    context: ResponseContext[object]


@dataclass(frozen=True, slots=True)
class MalformedSignal:
    signal: ResponseSignal[object]
    cause: Exception
    context: ResponseContext[object]


@dataclass(frozen=True, slots=True)
class AmbiguousSignal:
    signals: tuple[ResponseSignal[object], ...]
    context: ResponseContext[object]


type SignalOutcome = SignalMatch[object] | MalformedSignal | AmbiguousSignal | None


def inspect_signals(
    signals: tuple[ResponseSignal[Any], ...],
    context: ResponseContext[object],
    scope_context: ScopeContext,
) -> SignalOutcome:
    matches: list[SignalMatch[object]] = []
    for signal in signals:
        if not signal.scope.matches(scope_context):
            continue
        if signal.prefilter is not None and not signal.prefilter(context):
            continue
        result = signal.parser.bind(context).try_parse(signal.model)
        if isinstance(result, ParsedValue):
            matches.append(SignalMatch(signal, result.value, context))
        elif isinstance(result, Malformed) and signal.interception is SignalInterception.DEFINITIVE:
            return MalformedSignal(signal, result.cause, context)
        elif isinstance(result, NoMatch):
            continue
    if not matches:
        return None
    matches.sort(key=lambda item: item.signal.priority, reverse=True)
    highest = matches[0].signal.priority
    winners = [item for item in matches if item.signal.priority == highest]
    if len(winners) > 1 and all(
        item.signal.interception is SignalInterception.DEFINITIVE for item in winners
    ):
        return AmbiguousSignal(tuple(item.signal for item in winners), context)
    return winners[0]


class SolutionLocation(Enum):
    HEADER = "header"
    QUERY = "query"
    COOKIE = "cookie"
    BODY = "body"


@dataclass(frozen=True, slots=True)
class SolutionTarget:
    location: SolutionLocation
    name: str
    value_field: str | None = None


class ReplaySafety(Enum):
    SAFE_METHOD = "safe-method"
    IDEMPOTENCY_KEY = "idempotency-key"
    REJECTED_BEFORE_EXECUTION = "rejected-before-execution"
    REJECTED_BEFORE_ORIGIN = "rejected-before-origin"


class BodyReplayPolicy(Enum):
    REQUIRE_REPLAYABLE = "require-replayable"
    DENY = "deny"


class SolutionFreshness(Enum):
    PER_MATCH = "per-match"
    PER_CALL = "per-call"
    PER_ATTEMPT = "per-attempt"
    UNTIL_EXPIRY = "until-expiry"


@dataclass(frozen=True, slots=True)
class ReplayPolicy:
    max_replays: int
    safety: ReplaySafety
    proof_name: str | None = None
    body: BodyReplayPolicy = BodyReplayPolicy.REQUIRE_REPLAYABLE
    freshness: SolutionFreshness = SolutionFreshness.PER_MATCH


@dataclass(frozen=True, slots=True, eq=False)
class ResponseReaction[TEvent, TSolution]:
    source: Error[TEvent] | ResponseSignal[TEvent]
    solver: SolverRequirement[TEvent, TSolution]
    apply: SolutionTarget
    replay: ReplayPolicy
    priority: int = 0


@dataclass(frozen=True, slots=True)
class ReplayAction:
    patch: ValuePatch
    reaction: ResponseReaction[object, object]
    solution: object


class MissingSolverError(PlanError):
    pass


class ReplayDeniedError(PlanError):
    pass


class ReactionBudgetExceeded(PlanError):
    pass


@dataclass(slots=True)
class ReactionBudget:
    remaining: int

    def consume(self) -> None:
        if self.remaining <= 0:
            raise ReactionBudgetExceeded("response reaction budget exhausted")
        self.remaining -= 1


async def react(
    event: ErrorOutcome[object] | SignalMatch[object],
    reactions: tuple[ResponseReaction[object, object], ...],
    *,
    solvers: SolverRegistry,
    compiled: CompiledContract[object],
    values: OperationValues,
    solve_context: SolveContext,
    budget: ReactionBudget,
    body: BufferedBody | ReplayableBodyStream,
    origin_may_have_executed: bool,
) -> ReplayAction | None:
    source = event.case if isinstance(event, ErrorOutcome) else event.signal
    candidates = sorted(
        (reaction for reaction in reactions if reaction.source is source),
        key=lambda reaction: reaction.priority,
        reverse=True,
    )
    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0].priority == candidates[1].priority:
        raise PlanError("ambiguous response reactions with equal priority")
    reaction = candidates[0]
    solver = solvers.get(reaction.solver)
    if solver is None:
        return None
    ensure_replay_allowed(
        compiled,
        values,
        body,
        reaction.replay,
        origin_may_have_executed=origin_may_have_executed,
    )
    budget.consume()
    challenge = event.error if isinstance(event, ErrorOutcome) else event.value
    solution = await solver.solve(challenge, solve_context)
    patch = solution_patch(compiled, values, reaction.apply, solution)
    return ReplayAction(patch, reaction, solution)


def solution_patch(
    compiled: CompiledContract[object],
    values: OperationValues,
    target: SolutionTarget,
    solution: object,
) -> ValuePatch:
    selected = _select_solution(solution, target.value_field)
    if target.location is SolutionLocation.HEADER:
        slot = compiled.header_slots.get(target.name)
    elif target.location is SolutionLocation.QUERY:
        slot = compiled.query_slots.get(target.name)
    elif target.location is SolutionLocation.COOKIE:
        slot = compiled.cookie_slots.get(target.name)
    else:
        slot = compiled.body_field_slots.get(target.name)
        if slot is None:
            slot = compiled.body_slot
        if slot is compiled.body_slot and slot is not None:
            original = values.require(slot)
            selected = _replace_body_field(original, target.name, selected)
    if slot is None:
        raise PlanError(f"solution target is not declared: {target.location.value}.{target.name}")
    return ValuePatch((Set(slot, selected),))


def _validate_solution_target(compiled: CompiledContract[object], target: SolutionTarget) -> None:
    """Reject an application target that the endpoint did not declare, before any I/O."""
    if target.location is SolutionLocation.HEADER:
        slot = compiled.header_slots.get(target.name)
    elif target.location is SolutionLocation.QUERY:
        slot = compiled.query_slots.get(target.name)
    elif target.location is SolutionLocation.COOKIE:
        slot = compiled.cookie_slots.get(target.name)
    else:
        slot = compiled.body_field_slots.get(target.name) or compiled.body_slot
    if slot is None:
        raise PlanError(f"solution target is not declared: {target.location.value}.{target.name}")


def ensure_replay_allowed(
    compiled: CompiledContract[object],
    values: OperationValues,
    body: BufferedBody | ReplayableBodyStream,
    policy: ReplayPolicy,
    *,
    origin_may_have_executed: bool,
) -> None:
    if policy.max_replays <= 0:
        raise ReplayDeniedError("reaction replay is disabled")
    if policy.body is BodyReplayPolicy.DENY:
        raise ReplayDeniedError("body replay policy denies replay")
    if not isinstance(body, BufferedBody | ReplayableBodyStream):
        raise ReplayDeniedError("request body is not mechanically replayable")
    if not origin_may_have_executed:
        return
    method = compiled.contract.method.upper()
    if policy.safety is ReplaySafety.SAFE_METHOD and method in {"GET", "HEAD", "OPTIONS"}:
        return
    if policy.safety is ReplaySafety.IDEMPOTENCY_KEY and policy.proof_name is not None:
        slot = compiled.header_slots.get(policy.proof_name)
        if slot is not None and values.contains(slot):
            return
    if policy.safety in {
        ReplaySafety.REJECTED_BEFORE_EXECUTION,
        ReplaySafety.REJECTED_BEFORE_ORIGIN,
    }:
        return
    raise ReplayDeniedError(f"unsafe replay is not proven for {method}")


@dataclass(frozen=True, slots=True)
class CaptchaStep:
    case: object
    solve_with: SolverRequirement[object, object]
    verify: object


@dataclass(frozen=True, slots=True, eq=False)
class BeforeCall:
    acquire: object | None
    apply: SolutionTarget
    freshness: SolutionFreshness
    captcha: CaptchaStep | None = None
    challenge: object | None = None
    solver: SolverRequirement[Any, Any] | None = None


def validate_before_call_cycles(contracts: tuple[object, ...]) -> None:
    edges: dict[int, tuple[object, ...]] = {}
    for contract in contracts:
        flows = cast(tuple[BeforeCall, ...], getattr(contract, "before", ()))
        targets: list[object] = []
        for flow in flows:
            if flow.acquire is not None:
                targets.append(flow.acquire)
            if flow.captcha is not None:
                targets.append(flow.captcha.verify)
        edges[id(contract)] = tuple(targets)
    by_id = {id(contract): contract for contract in contracts}
    active: list[int] = []
    visited: set[int] = set()

    def visit(identity: int) -> None:
        if identity in active:
            start = active.index(identity)
            path = [
                getattr(by_id[item], "operation_id", repr(by_id[item]))
                for item in (*active[start:], identity)
            ]
            raise GraphError("before-call cycle: " + " -> ".join(path))
        if identity in visited:
            return
        visited.add(identity)
        active.append(identity)
        for target in edges.get(identity, ()):
            if id(target) in by_id:
                visit(id(target))
        active.pop()

    for contract in contracts:
        visit(id(contract))


def header_target(name: str, *, value_field: str | None = None) -> SolutionTarget:
    return SolutionTarget(SolutionLocation.HEADER, name, value_field)


def query_target(name: str, *, value_field: str | None = None) -> SolutionTarget:
    return SolutionTarget(SolutionLocation.QUERY, name, value_field)


def cookie_target(name: str, *, value_field: str | None = None) -> SolutionTarget:
    return SolutionTarget(SolutionLocation.COOKIE, name, value_field)


def body_field(name: str, *, value_field: str | None = None) -> SolutionTarget:
    return SolutionTarget(SolutionLocation.BODY, name, value_field)


def safe_method(*, max_replays: int = 1) -> ReplayPolicy:
    return ReplayPolicy(max_replays, ReplaySafety.SAFE_METHOD)


def idempotency_key(name: str, *, max_replays: int = 1) -> ReplayPolicy:
    return ReplayPolicy(max_replays, ReplaySafety.IDEMPOTENCY_KEY, proof_name=name)


def rejected_before_execution(*, max_replays: int = 1) -> ReplayPolicy:
    return ReplayPolicy(max_replays, ReplaySafety.REJECTED_BEFORE_EXECUTION)


def rejected_before_origin(*, max_replays: int = 1) -> ReplayPolicy:
    return ReplayPolicy(max_replays, ReplaySafety.REJECTED_BEFORE_ORIGIN)


def _select_solution(solution: object, field_name: str | None) -> object:
    if field_name is None:
        return solution
    if isinstance(solution, Mapping):
        try:
            return solution[field_name]
        except KeyError as exc:
            raise PlanError(f"solution field is missing: {field_name}") from exc
    try:
        return getattr(solution, field_name)
    except AttributeError as exc:
        raise PlanError(f"solution field is missing: {field_name}") from exc


def _replace_body_field(body: object, name: str, value: object) -> object:
    if isinstance(body, Mapping):
        return {**body, name: value}
    models = default_model_adapters()
    try:
        dumped = models.dump_model(body)
    except TypeError as exc:
        raise PlanError("body solution target requires a mapping or registered model") from exc
    if not isinstance(dumped, Mapping):
        raise PlanError("body model adapter must produce a mapping")
    return models.load(type(body), {**dumped, name: value})
