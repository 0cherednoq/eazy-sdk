"""Vendor-neutral signals, reactions, before-call flows and replay safety."""

from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, cast, runtime_checkable

from eazy_sdk._internal.errors import PlanError
from eazy_sdk._internal.http_compiler import CompiledContract
from eazy_sdk._internal.http_plan import RequestScope, ScopeContext
from eazy_sdk._internal.kernel import OperationIdentity, OperationValues, Set, ValuePatch
from eazy_sdk.request.prepared import BufferedBody, ReplayableBodyStream
from eazy_sdk.response import ResponseContext
from eazy_sdk.response.cases import (
    Malformed,
    NoMatch,
    ParsedValue,
    ResponseParser,
)


class ProtectionConfigurationError(PlanError):
    """Protection declaration cannot be compiled safely before transport I/O."""


class ChallengeDetectionError(PlanError):
    def __init__(self, policy: str, attempt: int) -> None:
        self.policy = policy
        self.attempt = attempt
        super().__init__(policy, attempt)

    def __str__(self) -> str:
        return f"challenge detection failed for {self.policy} on attempt {self.attempt}"


class ChallengeSolveError(PlanError):
    def __init__(self, policy: str, attempt: int) -> None:
        self.policy = policy
        self.attempt = attempt
        super().__init__(policy, attempt)

    def __str__(self) -> str:
        return f"challenge solve failed for {self.policy} on attempt {self.attempt}"


class ChallengeApplicationError(PlanError):
    def __init__(self, policy: str) -> None:
        self.policy = policy
        super().__init__(policy)

    def __str__(self) -> str:
        return f"challenge solution application failed for {self.policy}"


class MalformedChallengeError(ValueError):
    """A simple detector recognized a challenge whose payload is malformed."""


@dataclass(frozen=True, slots=True)
class SolveContext:
    operation: OperationIdentity
    response: ResponseContext[object] | None
    attempt: int
    deadline: datetime | None = None


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


def _inspect_signals(
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


class ReplaySafety(Enum):
    SAFE_METHOD = "safe-method"
    IDEMPOTENCY_KEY = "idempotency-key"
    REJECTED_BEFORE_EXECUTION = "rejected-before-execution"
    REJECTED_BEFORE_ORIGIN = "rejected-before-origin"


class BodyReplayPolicy(Enum):
    REQUIRE_REPLAYABLE = "require-replayable"
    DENY = "deny"


class ProtectionPersistenceMode(Enum):
    PER_MATCH = "per-match"
    PER_CALL = "per-call"
    PER_ATTEMPT = "per-attempt"
    UNTIL_EXPIRY = "until-expiry"
    UNTIL_REJECTED = "until-rejected"


class ProtectionStateScope(Enum):
    CLIENT = "client"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class ProtectionStateKey:
    """Declare which client/session/network identity owns managed protection state."""

    scope: ProtectionStateScope


def client_identity() -> ProtectionStateKey:
    return ProtectionStateKey(ProtectionStateScope.CLIENT)


def session_identity() -> ProtectionStateKey:
    return ProtectionStateKey(ProtectionStateScope.SESSION)


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
        identity=identity or solver.name,
        revision=revision,
        scope=scope,
        signal=signal,
        solver=solver,
        apply=apply,
        persistence=persistence,
        replay=replay,
        challenge_identity=challenge_identity,
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
        identity=identity,
        revision=revision,
        scope=scope,
        acquire=acquire,
        challenge=challenge,
        solver=solver,
        apply=apply,
        persistence=persistence,
    )


@dataclass(frozen=True, slots=True)
class ProtectionBundle:
    operation_protections: tuple[ProtectionFlow[Any], ...] = ()
    before_call_policies: tuple[BeforeCallPolicy[Any, Any], ...] = ()
    challenge_policies: tuple[ChallengePolicy[Any, Any], ...] = ()
    operation_solver_bindings: tuple[SolverBinding[Any, Any], ...] = ()
    challenge_solver_bindings: tuple[ChallengeSolverBinding[Any, Any], ...] = ()

    def __post_init__(self) -> None:
        if not any(
            (
                self.operation_protections,
                self.before_call_policies,
                self.challenge_policies,
            )
        ):
            raise ValueError("protection bundle must contain at least one policy or flow")


@runtime_checkable
class InstallableProtection(Protocol):
    def to_bundle(self) -> ProtectionBundle: ...


@dataclass(frozen=True, slots=True)
class ReplayPolicy:
    max_replays: int
    safety: ReplaySafety
    proof_name: str | None = None
    body: BodyReplayPolicy = BodyReplayPolicy.REQUIRE_REPLAYABLE

    def __post_init__(self) -> None:
        if self.max_replays < 0:
            raise ValueError("replay budget cannot be negative")
        if self.safety is ReplaySafety.IDEMPOTENCY_KEY:
            if self.proof_name is None or not _HTTP_FIELD_NAME.fullmatch(self.proof_name):
                raise ValueError("idempotency proof must be a valid HTTP field name")
        elif self.proof_name is not None:
            raise ValueError("replay proof applies only to idempotency-key safety")


class MissingSolverError(ProtectionConfigurationError):
    pass


class ReplayDeniedError(PlanError):
    pass


def _private_bindings_patch[TSolution](
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


def _ensure_replay_allowed(
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


def safe_method(*, max_replays: int = 1) -> ReplayPolicy:
    return ReplayPolicy(max_replays, ReplaySafety.SAFE_METHOD)


def idempotency_key(name: str, *, max_replays: int = 1) -> ReplayPolicy:
    return ReplayPolicy(max_replays, ReplaySafety.IDEMPOTENCY_KEY, proof_name=name)


def rejected_before_execution(*, max_replays: int = 1) -> ReplayPolicy:
    return ReplayPolicy(max_replays, ReplaySafety.REJECTED_BEFORE_EXECUTION)


def rejected_before_origin(*, max_replays: int = 1) -> ReplayPolicy:
    return ReplayPolicy(max_replays, ReplaySafety.REJECTED_BEFORE_ORIGIN)


@dataclass(frozen=True, slots=True)
class SolutionCookieSet:
    field: str = "cookies"
    name_field: str = "name"
    value_field: str = "value"

    def __post_init__(self) -> None:
        if not self.field or not self.name_field or not self.value_field:
            raise ValueError("solution cookie-set fields must not be empty")


def solution_cookie_set(
    *,
    field: str = "cookies",
    name_field: str = "name",
    value_field: str = "value",
) -> SolutionCookieSet:
    return SolutionCookieSet(field, name_field, value_field)


@dataclass(frozen=True, slots=True)
class SolutionFields[TSolution]:
    """Declarative, compiler-reserved destinations for one complete solution batch."""

    _bindings: PrivateBindings[TSolution]


def solution_fields[TSolution](
    *,
    headers: Mapping[str, str | None] | None = None,
    query: Mapping[str, str | None] | None = None,
    cookies: Mapping[str, str | None] | None = None,
    body: Mapping[str, str | None] | None = None,
    cookie_set: SolutionCookieSet | None = None,
) -> SolutionFields[TSolution]:
    """Map public destination names to fields selected from a solver result."""

    targets: list[PrivateBindingTarget] = []
    for destinations, factory in (
        (headers, private_header),
        (query, private_query),
        (cookies, private_cookie),
        (body, private_body),
    ):
        if destinations is not None:
            targets.extend(
                factory(name, field=field) for name, field in destinations.items()
            )
    if cookie_set is not None:
        targets.append(
            private_cookie_set(
                field=cookie_set.field,
                name_field=cookie_set.name_field,
                value_field=cookie_set.value_field,
            )
        )
    if not targets:
        raise ProtectionConfigurationError(
            "solution_fields requires at least one declared destination"
        )
    return SolutionFields(PrivateBindings(*targets))


@dataclass(frozen=True, slots=True)
class _SimpleDetectorParser[TChallenge]:
    policy: str
    callback: Callable[[ResponseContext[object]], TChallenge | None]

    def bind(self, response: ResponseContext[object]) -> _BoundSimpleDetectorParser[TChallenge]:
        return _BoundSimpleDetectorParser(response, self.policy, self.callback)


@dataclass(frozen=True, slots=True)
class _BoundSimpleDetectorParser[TChallenge]:
    response: ResponseContext[object]
    policy: str
    callback: Callable[[ResponseContext[object]], TChallenge | None]

    def try_parse[T](self, model: type[T]) -> ParsedValue[T] | NoMatch | Malformed:
        if model is not object:
            return NoMatch()
        try:
            value = self.callback(self.response)
        except MalformedChallengeError as exc:
            return Malformed(exc)
        except Exception as exc:
            raise ChallengeDetectionError(
                self.policy,
                self.response.attempt.number,
            ) from exc
        if value is None:
            return NoMatch()
        return ParsedValue(cast(T, value))


@dataclass(frozen=True, slots=True)
class _ChallengeGuard[TChallenge, TSolution]:
    policy: ChallengePolicySpec[TChallenge, TSolution]
    binding: ChallengeSolverBinding[TChallenge, TSolution]

    def to_bundle(self) -> ProtectionBundle:
        return ProtectionBundle(
            challenge_policies=(self.policy,),
            challenge_solver_bindings=(self.binding,),
        )


def challenge_guard[TChallenge, TSolution](
    *,
    name: str,
    scope: RequestScope,
    detect: Callable[[ResponseContext[object]], TChallenge | None],
    solver: ChallengeSolver[TChallenge, TSolution],
    apply: SolutionFields[TSolution],
    replay: ReplayPolicy | None = None,
    revision: int = 1,
) -> InstallableProtection:
    """Lower one typed detector/solver/application guard into the shared executor."""

    if not name:
        raise ProtectionConfigurationError("challenge guard name must not be empty")
    if not callable(detect):
        raise ProtectionConfigurationError("challenge guard detector must be callable")
    if not callable(getattr(solver, "solve", None)):
        raise ProtectionConfigurationError("challenge guard solver must define solve()")
    if not isinstance(apply, SolutionFields):
        raise ProtectionConfigurationError(
            "challenge guard application must be created by solution_fields()"
        )
    requirement = SolverRequirement[TChallenge, TSolution](name)
    signal = ResponseSignal(
        f"{name}.response",
        scope,
        cast(type[TChallenge], object),
        _SimpleDetectorParser(name, detect),
    )
    policy = challenge_policy(
        identity=name,
        revision=revision,
        scope=scope,
        signal=signal,
        solver=requirement,
        apply=apply._bindings,
        persistence=per_match(),
        replay=replay or safe_method(max_replays=1),
    )
    return _ChallengeGuard(policy, bind_challenge_solver(requirement, solver))


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


_HTTP_FIELD_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


__all__ = [
    "AmbiguousSignal",
    "BeforeCallPolicy",
    "BeforeCallPolicySpec",
    "BodyReplayPolicy",
    "ChallengePolicy",
    "ChallengePolicySpec",
    "ChallengeSolverBinding",
    "ChallengeSolverBindings",
    "FromProtection",
    "MalformedSignal",
    "PrivateBinding",
    "PrivateBindingTarget",
    "PrivateBindings",
    "PrivateCookieSetBinding",
    "ProtectionBundle",
    "ProtectionFlow",
    "ProtectionPersistence",
    "ProtectionPersistenceMode",
    "ProtectionRequirement",
    "ProtectionSolver",
    "ProtectionStateKey",
    "ProtectionStateScope",
    "ReplaySafety",
    "ResponseSignal",
    "SignalInterception",
    "SignalMatch",
    "SignalOutcome",
    "SolutionLocation",
    "SolverBinding",
    "SolverBindings",
    "SolverRequirement",
    "before_call_policy",
    "bind_challenge_solver",
    "bind_solver",
    "challenge_policy",
    "client_identity",
    "per_attempt",
    "per_call",
    "per_match",
    "private_bindings",
    "private_body",
    "private_cookie",
    "private_cookie_set",
    "private_header",
    "private_query",
    "protection_flow",
    "session_identity",
    "until_expiry",
    "until_rejected",
]
