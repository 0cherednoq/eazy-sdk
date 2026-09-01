"""Vendor-neutral signals, reactions, before-call flows and replay safety."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, cast, runtime_checkable

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


@dataclass(frozen=True, slots=True)
class ChallengeSolverBinding[TChallenge, TSolution]:
    """Bind one conditional/before-call solver requirement to its implementation."""

    requirement: SolverRequirement[TChallenge, TSolution]
    solver: ChallengeSolver[TChallenge, TSolution]


def bind_challenge_solver[TChallenge, TSolution](
    requirement: SolverRequirement[TChallenge, TSolution],
    solver: ChallengeSolver[TChallenge, TSolution],
) -> ChallengeSolverBinding[TChallenge, TSolution]:
    return ChallengeSolverBinding(requirement, solver)


@dataclass(frozen=True, slots=True)
class ChallengeSolverBindings:
    """Immutable solver bindings used by typed protection policies."""

    bindings: tuple[ChallengeSolverBinding[Any, Any], ...] = ()

    def __init__(self, *bindings: ChallengeSolverBinding[Any, Any]) -> None:
        identities = [id(binding.requirement) for binding in bindings]
        if len(identities) != len(set(identities)):
            raise ValueError("a challenge solver requirement can be bound only once")
        object.__setattr__(self, "bindings", bindings)

    def get[TChallenge, TSolution](
        self,
        requirement: SolverRequirement[TChallenge, TSolution],
    ) -> ChallengeSolver[TChallenge, TSolution] | None:
        for binding in self.bindings:
            if binding.requirement is requirement:
                return cast(ChallengeSolver[TChallenge, TSolution], binding.solver)
        return None


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


class ProtectionPersistenceMode(Enum):
    PER_MATCH = "per-match"
    PER_CALL = "per-call"
    PER_ATTEMPT = "per-attempt"
    UNTIL_EXPIRY = "until-expiry"
    UNTIL_REJECTED = "until-rejected"


class ProtectionStateScope(Enum):
    CLIENT = "client"
    SESSION = "session"
    NETWORK_IDENTITY = "network-identity"


@dataclass(frozen=True, slots=True)
class ProtectionStateKey:
    """Declare which client/session/network identity owns managed protection state."""

    scope: ProtectionStateScope


def client_identity() -> ProtectionStateKey:
    return ProtectionStateKey(ProtectionStateScope.CLIENT)


def session_identity() -> ProtectionStateKey:
    return ProtectionStateKey(ProtectionStateScope.SESSION)


def network_identity() -> ProtectionStateKey:
    return ProtectionStateKey(ProtectionStateScope.NETWORK_IDENTITY)


@dataclass(frozen=True, slots=True)
class ProtectionPersistence:
    mode: ProtectionPersistenceMode
    scope: ProtectionStateKey = ProtectionStateKey(ProtectionStateScope.CLIENT)


_CLIENT_STATE_KEY = ProtectionStateKey(ProtectionStateScope.CLIENT)


def per_match() -> ProtectionPersistence:
    return ProtectionPersistence(ProtectionPersistenceMode.PER_MATCH)


def per_call() -> ProtectionPersistence:
    return ProtectionPersistence(ProtectionPersistenceMode.PER_CALL)


def per_attempt() -> ProtectionPersistence:
    return ProtectionPersistence(ProtectionPersistenceMode.PER_ATTEMPT)


def until_expiry(
    *, scope: ProtectionStateKey = _CLIENT_STATE_KEY
) -> ProtectionPersistence:
    return ProtectionPersistence(ProtectionPersistenceMode.UNTIL_EXPIRY, scope)


def until_rejected(
    *, scope: ProtectionStateKey = _CLIENT_STATE_KEY
) -> ProtectionPersistence:
    return ProtectionPersistence(ProtectionPersistenceMode.UNTIL_REJECTED, scope)


@dataclass(frozen=True, slots=True)
class PrivateBinding:
    """One compiler-reserved private write that is absent from the public call signature."""

    location: SolutionLocation
    name: str
    field: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("private binding target name must not be empty")
        if self.field == "":
            raise ValueError("private binding source field must not be empty")


@dataclass(frozen=True, slots=True)
class PrivateCookieSetBinding:
    """Expand an iterable solution field into a managed set of named cookies."""

    field: str = "cookies"
    name_field: str = "name"
    value_field: str = "value"

    def __post_init__(self) -> None:
        if not self.field or not self.name_field or not self.value_field:
            raise ValueError("private cookie-set fields must not be empty")


type PrivateBindingTarget = PrivateBinding | PrivateCookieSetBinding


@dataclass(frozen=True, slots=True)
class PrivateBindings[TSolution]:
    targets: tuple[PrivateBindingTarget, ...]

    def __init__(self, *targets: PrivateBindingTarget) -> None:
        if not targets:
            raise ValueError("private bindings require at least one target")
        if len(targets) != len(set(targets)):
            raise ValueError("private binding targets must be unique")
        object.__setattr__(self, "targets", targets)


def private_bindings[TSolution](
    *targets: PrivateBindingTarget,
) -> PrivateBindings[TSolution]:
    return PrivateBindings(*targets)


def private_header(name: str, *, field: str | None = None) -> PrivateBinding:
    return PrivateBinding(SolutionLocation.HEADER, name, field)


def private_query(name: str, *, field: str | None = None) -> PrivateBinding:
    return PrivateBinding(SolutionLocation.QUERY, name, field)


def private_cookie(name: str, *, field: str | None = None) -> PrivateBinding:
    return PrivateBinding(SolutionLocation.COOKIE, name, field)


def private_body(name: str, *, field: str | None = None) -> PrivateBinding:
    return PrivateBinding(SolutionLocation.BODY, name, field)


def private_cookie_set(
    *,
    field: str = "cookies",
    name_field: str = "name",
    value_field: str = "value",
) -> PrivateCookieSetBinding:
    return PrivateCookieSetBinding(field, name_field, value_field)


@runtime_checkable
class ChallengePolicy[TChallenge, TSolution](Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def revision(self) -> int: ...

    @property
    def scope(self) -> RequestScope: ...

    @property
    def signal(self) -> ResponseSignal[TChallenge]: ...

    @property
    def solver(self) -> SolverRequirement[TChallenge, TSolution]: ...

    @property
    def apply(self) -> PrivateBindings[TSolution]: ...

    @property
    def persistence(self) -> ProtectionPersistence: ...

    @property
    def replay(self) -> ReplayPolicy: ...

    @property
    def challenge_identity(self) -> Callable[[TChallenge], Hashable] | None: ...


@dataclass(frozen=True, slots=True)
class ChallengePolicySpec[TChallenge, TSolution]:
    identity: str
    revision: int
    scope: RequestScope
    signal: ResponseSignal[TChallenge]
    solver: SolverRequirement[TChallenge, TSolution]
    apply: PrivateBindings[TSolution]
    persistence: ProtectionPersistence
    replay: ReplayPolicy
    challenge_identity: Callable[[TChallenge], Hashable] | None = None

    def __post_init__(self) -> None:
        if not self.identity:
            raise ValueError("challenge policy identity must not be empty")
        if self.revision < 1:
            raise ValueError("challenge policy revision must be positive")
        if self.signal.scope != self.scope:
            raise ValueError("challenge policy and signal scopes must match")


def challenge_policy[TChallenge, TSolution](
    *,
    scope: RequestScope,
    signal: ResponseSignal[TChallenge],
    solver: SolverRequirement[TChallenge, TSolution],
    apply: PrivateBindings[TSolution],
    persistence: ProtectionPersistence,
    replay: ReplayPolicy,
    identity: str | None = None,
    revision: int = 1,
    challenge_identity: Callable[[TChallenge], Hashable] | None = None,
) -> ChallengePolicySpec[TChallenge, TSolution]:
    return ChallengePolicySpec(
        identity or solver.name,
        revision,
        scope,
        signal,
        solver,
        apply,
        persistence,
        replay,
        challenge_identity,
    )


@runtime_checkable
class BeforeCallPolicy[TChallenge, TSolution](Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def revision(self) -> int: ...

    @property
    def scope(self) -> RequestScope: ...

    @property
    def acquire(self) -> object | None: ...

    @property
    def challenge(self) -> TChallenge | None: ...

    @property
    def solver(self) -> SolverRequirement[TChallenge, TSolution] | None: ...

    @property
    def apply(self) -> PrivateBindings[TSolution]: ...

    @property
    def persistence(self) -> ProtectionPersistence: ...


@dataclass(frozen=True, slots=True)
class BeforeCallPolicySpec[TChallenge, TSolution]:
    identity: str
    revision: int
    scope: RequestScope
    acquire: object | None
    challenge: TChallenge | None
    solver: SolverRequirement[TChallenge, TSolution] | None
    apply: PrivateBindings[TSolution]
    persistence: ProtectionPersistence

    def __post_init__(self) -> None:
        if not self.identity:
            raise ValueError("before-call policy identity must not be empty")
        if self.revision < 1:
            raise ValueError("before-call policy revision must be positive")
        has_acquire = self.acquire is not None
        has_solver = self.challenge is not None and self.solver is not None
        if has_acquire == has_solver:
            raise ValueError(
                "before-call policy requires exactly one of acquire or challenge+solver"
            )


def before_call_policy[TChallenge, TSolution](
    *,
    identity: str,
    scope: RequestScope,
    apply: PrivateBindings[TSolution],
    persistence: ProtectionPersistence,
    acquire: object | None = None,
    challenge: TChallenge | None = None,
    solver: SolverRequirement[TChallenge, TSolution] | None = None,
    revision: int = 1,
) -> BeforeCallPolicySpec[TChallenge, TSolution]:
    return BeforeCallPolicySpec(
        identity,
        revision,
        scope,
        acquire,
        challenge,
        solver,
        apply,
        persistence,
    )


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


def private_bindings_patch[TSolution](
    compiled: CompiledContract[object],
    bindings: PrivateBindings[TSolution],
    solution: TSolution,
) -> ValuePatch:
    """Validate every managed destination, then return one all-or-nothing patch."""

    from eazy_sdk._internal.http_compiler import ManagedCookieSetDescriptor

    operations: list[Set[object]] = []
    fixed_cookie_names = {
        name
        for name, slot in compiled.cookie_slots.items()
        if not isinstance(compiled.descriptors[slot], ManagedCookieSetDescriptor)
    }
    dynamic_names: set[str] = set()
    for target in bindings.targets:
        slot = compiled.private_binding_slots.get(target)
        if slot is None:
            raise PlanError("private binding target was not reserved by the compiler")
        if isinstance(target, PrivateCookieSetBinding):
            raw = _select_solution(solution, target.field)
            if isinstance(raw, str | bytes | bytearray) or not isinstance(raw, Iterable):
                raise PlanError("private cookie-set source must be an iterable")
            cookies: list[tuple[str, str]] = []
            for item in raw:
                name = _select_solution(item, target.name_field)
                value = _select_solution(item, target.value_field)
                if not isinstance(name, str) or not name:
                    raise PlanError("private cookie-set item has an invalid name")
                if not isinstance(value, str):
                    raise PlanError(f"private cookie {name!r} value must be a string")
                if any(character in name for character in "\r\n\x00;= "):
                    raise PlanError("private cookie-set item has an invalid name")
                if any(character in value for character in "\r\n\x00"):
                    raise PlanError(f"private cookie {name!r} has an invalid value")
                if name in fixed_cookie_names or name in dynamic_names:
                    raise PlanError(f"private cookie destination conflicts: {name!r}")
                dynamic_names.add(name)
                cookies.append((name, value))
            operations.append(Set(slot, tuple(cookies)))
            continue
        selected = _select_solution(solution, target.field)
        if target.location is SolutionLocation.HEADER:
            rendered = str(selected)
            if any(character in rendered for character in "\r\n\x00"):
                raise PlanError(f"private header {target.name!r} has an invalid value")
        elif target.location is SolutionLocation.COOKIE:
            rendered = str(selected)
            if any(character in rendered for character in "\r\n\x00"):
                raise PlanError(f"private cookie {target.name!r} has an invalid value")
        operations.append(Set(slot, selected))
    return ValuePatch(tuple(operations))


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
