"""Vendor-neutral signals, reactions, before-call flows and replay safety."""

from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Awaitable, Callable, Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType, UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    runtime_checkable,
)

from eazy_sdk.core.errors import ConfigurationError, EazySdkError, PlanError
from eazy_sdk.core.http_plan import RequestScope, ScopeContext
from eazy_sdk.core.kernel import OperationIdentity, OperationValues, Set, ValuePatch
from eazy_sdk.redaction import redact_url_credentials
from eazy_sdk.request.prepared import BufferedBody, ReplayableBodyStream
from eazy_sdk.response import NormalizedResponse, ResponseContext
from eazy_sdk.response.cases import (
    Malformed,
    NoMatch,
    ParsedValue,
    ResponseParser,
)

if TYPE_CHECKING:
    from eazy_sdk.compile.http_compiler import CompiledContract


class ProtectionConfigurationError(PlanError, ConfigurationError):
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


class MalformedChallengeError(EazySdkError, ValueError):
    """Raised by a detector: the response is a challenge but its payload is malformed."""


class ChallengeMalformedError(PlanError):
    """A policy recognized a challenge whose payload is malformed; the call cannot proceed."""

    def __init__(self, policy: str, attempt: int) -> None:
        self.policy = policy
        self.attempt = attempt
        super().__init__(policy, attempt)

    def __str__(self) -> str:
        return f"malformed challenge for {self.policy} on attempt {self.attempt}"


class AmbiguousChallengeError(ProtectionConfigurationError):
    """Two or more definitive policies matched one response with equal priority."""

    def __init__(self, policies: tuple[str, ...], attempt: int) -> None:
        self.policies = policies
        self.attempt = attempt
        super().__init__(
            "ambiguous challenge on attempt "
            f"{attempt}: policies {', '.join(policies)} matched with equal priority"
        )


class ProtectedFetch(Protocol):
    """Send one raw request through the guarded client's own transport session.

    The executor implements this port over the same handler, proxy and cookie jar
    that emitted the challenged request. Requests sent through it bypass guards,
    reactions and replay, so a solver can download challenge assets or POST a
    validation form without recursing into the protection pipeline.
    """

    async def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> NormalizedResponse[object]: ...


@dataclass(frozen=True, slots=True)
class TransportIdentity:
    """Network identity the runtime actually used for the triggering request."""

    user_agent: str | None = None
    proxy: str | None = None
    impersonation: str | None = None

    def __repr__(self) -> str:
        return (
            "TransportIdentity("
            f"user_agent={self.user_agent!r}, "
            f"proxy={redact_url_credentials(self.proxy)!r}, "
            f"impersonation={self.impersonation!r})"
        )

    def fingerprint(self) -> str:
        """Stable, secret-free digest used to bind managed state to this identity."""

        digest = hashlib.sha256()
        for component in (self.user_agent, self.proxy, self.impersonation):
            digest.update(b"\x00" if component is None else b"\x01" + component.encode("utf-8"))
            digest.update(b"\x1f")
        return digest.hexdigest()


class _UnavailableFetch:
    async def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> NormalizedResponse[object]:
        raise ProtectionConfigurationError(
            "solve context was created without a transport; "
            "SolveContext.fetch is available only inside a running client"
        )


_UNAVAILABLE_FETCH: ProtectedFetch = _UnavailableFetch()
_NO_HEADERS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class SolveContext:
    operation: OperationIdentity
    response: ResponseContext[object] | None
    attempt: int
    deadline: datetime | None = None
    fetch: ProtectedFetch = _UNAVAILABLE_FETCH
    identity: TransportIdentity = field(default_factory=TransportIdentity)
    request_headers: Mapping[str, str] = _NO_HEADERS


class ChallengeSolver[TChallenge, TSolution](Protocol):
    """Solve one typed challenge; used by response guards, before-call and mandatory flows."""

    async def solve(self, challenge: TChallenge, context: SolveContext) -> TSolution: ...


@dataclass(frozen=True, slots=True, eq=False)
class SolverRequirement[TChallenge, TSolution]:
    """Typed identity of a solver a policy or mandatory flow requires."""

    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("solver requirement name must not be empty")


@dataclass(frozen=True, slots=True)
class FromProtection:
    """Populate one exact wire-model field from a mandatory protection result."""

    requirement: SolverRequirement[Any, Any]
    field: str

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("protection result field must not be empty")


@dataclass(frozen=True, slots=True)
class SolverBinding[TChallenge, TSolution]:
    """Bind one solver requirement to its implementation."""

    requirement: SolverRequirement[TChallenge, TSolution]
    solver: ChallengeSolver[TChallenge, TSolution]


def bind_solver[TChallenge, TSolution](
    requirement: SolverRequirement[TChallenge, TSolution],
    solver: ChallengeSolver[TChallenge, TSolution],
) -> SolverBinding[TChallenge, TSolution]:
    return SolverBinding(requirement, solver)


@dataclass(frozen=True, slots=True)
class SolverBindings:
    """Immutable registry of solver bindings keyed by requirement identity."""

    bindings: tuple[SolverBinding[Any, Any], ...] = ()

    def __init__(self, *bindings: SolverBinding[Any, Any]) -> None:
        identities = [id(binding.requirement) for binding in bindings]
        if len(identities) != len(set(identities)):
            raise ValueError("a solver requirement can be bound only once")
        object.__setattr__(self, "bindings", bindings)

    def get[TChallenge, TSolution](
        self,
        requirement: SolverRequirement[TChallenge, TSolution],
    ) -> ChallengeSolver[TChallenge, TSolution] | None:
        for binding in self.bindings:
            if binding.requirement is requirement:
                return cast(ChallengeSolver[TChallenge, TSolution], binding.solver)
        return None

    def __iter__(self) -> Iterator[SolverBinding[Any, Any]]:
        return iter(self.bindings)

    def __len__(self) -> int:
        return len(self.bindings)


class OperationReference(Protocol):
    """A decorated API method (or its declaration) usable as acquire/verify step."""

    @property
    def declaration(self) -> object: ...


@dataclass(frozen=True, slots=True)
class ProtectionFlow[TResult]:
    """Bounded acquire -> optional solve -> optional verify flow."""

    requirement: SolverRequirement[Any, TResult]
    acquire: OperationReference
    solve: bool = False
    verify: OperationReference | None = None


def protection_flow[TResult](
    requirement: SolverRequirement[Any, TResult],
    *,
    acquire: OperationReference,
    solve: bool = False,
    verify: OperationReference | None = None,
) -> ProtectionFlow[TResult]:
    return ProtectionFlow(requirement, acquire, solve, verify)


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


@dataclass(frozen=True, slots=True, kw_only=True)
class ChallengePolicy[TChallenge, TSolution]:
    """Conditional policy: a response signal, a solver and an atomic application."""

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
        if not isinstance(self.signal, ResponseSignal):
            raise TypeError("challenge policy signal must be ResponseSignal")
        if not isinstance(self.solver, SolverRequirement):
            raise TypeError("challenge policy solver must be SolverRequirement")
        if not isinstance(self.apply, PrivateBindings):
            raise TypeError("challenge policy apply must be PrivateBindings")
        if not isinstance(self.persistence, ProtectionPersistence):
            raise TypeError("challenge policy persistence must be ProtectionPersistence")
        if not isinstance(self.replay, ReplayPolicy):
            raise TypeError("challenge policy replay must be ReplayPolicy")
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
) -> ChallengePolicy[TChallenge, TSolution]:
    return ChallengePolicy(
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


@dataclass(frozen=True, slots=True, kw_only=True)
class BeforeCallPolicy[TChallenge, TSolution]:
    """Proactive policy: acquire via an operation or solve a fixed challenge before a call."""

    identity: str
    revision: int
    scope: RequestScope
    acquire: OperationReference | None
    challenge: TChallenge | None
    solver: SolverRequirement[TChallenge, TSolution] | None
    apply: PrivateBindings[TSolution]
    persistence: ProtectionPersistence

    def __post_init__(self) -> None:
        if not self.identity:
            raise ValueError("before-call policy identity must not be empty")
        if self.revision < 1:
            raise ValueError("before-call policy revision must be positive")
        if not isinstance(self.apply, PrivateBindings):
            raise TypeError("before-call policy apply must be PrivateBindings")
        if not isinstance(self.persistence, ProtectionPersistence):
            raise TypeError("before-call policy persistence must be ProtectionPersistence")
        if self.solver is not None and not isinstance(self.solver, SolverRequirement):
            raise TypeError("before-call policy solver must be SolverRequirement")
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
    acquire: OperationReference | None = None,
    challenge: TChallenge | None = None,
    solver: SolverRequirement[TChallenge, TSolution] | None = None,
    revision: int = 1,
) -> BeforeCallPolicy[TChallenge, TSolution]:
    return BeforeCallPolicy(
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
    """Complete protection configuration: flows, policies and their solver bindings.

    Installable guards lower into one bundle; ``ClientConfig(protection=...)`` holds the merged
    bundle of a client. ``solver_bindings`` accepts a tuple of ``SolverBinding`` or a
    ``SolverBindings`` registry and is normalized to a tuple.
    """

    operation_protections: tuple[ProtectionFlow[Any], ...] = ()
    before_call_policies: tuple[BeforeCallPolicy[Any, Any], ...] = ()
    challenge_policies: tuple[ChallengePolicy[Any, Any], ...] = ()
    solver_bindings: tuple[SolverBinding[Any, Any], ...] | SolverBindings = ()

    def __post_init__(self) -> None:
        bindings = self.solver_bindings
        if isinstance(bindings, SolverBindings):
            object.__setattr__(self, "solver_bindings", bindings.bindings)
        else:
            object.__setattr__(self, "solver_bindings", tuple(bindings))
        for name, expected in (
            ("operation_protections", ProtectionFlow),
            ("before_call_policies", BeforeCallPolicy),
            ("challenge_policies", ChallengePolicy),
            ("solver_bindings", SolverBinding),
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                object.__setattr__(self, name, tuple(values))
                values = getattr(self, name)
            if any(not isinstance(item, expected) for item in values):
                raise TypeError(
                    f"{name} contains a malformed policy; expected {expected.__name__} values"
                )
        # A registry validates that one requirement is bound only once.
        SolverBindings(*self.solver_bindings)
        identities = [policy.identity for policy in self.before_call_policies]
        identities.extend(policy.identity for policy in self.challenge_policies)
        duplicates = sorted({item for item in identities if identities.count(item) > 1})
        if duplicates:
            raise ValueError("duplicate protection policy identity: " + ", ".join(duplicates))
        requirements = [id(flow.requirement) for flow in self.operation_protections]
        if len(requirements) != len(set(requirements)):
            raise ValueError("duplicate operation protection requirement")

    def __bool__(self) -> bool:
        return bool(
            self.operation_protections or self.before_call_policies or self.challenge_policies
        )

    def merge(self, *others: ProtectionBundle) -> ProtectionBundle:
        """Concatenate bundles; duplicate identities or requirements are rejected."""

        bundles = (self, *others)
        return ProtectionBundle(
            operation_protections=tuple(f for b in bundles for f in b.operation_protections),
            before_call_policies=tuple(p for b in bundles for p in b.before_call_policies),
            challenge_policies=tuple(p for b in bundles for p in b.challenge_policies),
            solver_bindings=tuple(s for b in bundles for s in b.solver_bindings),
        )

    @property
    def solvers(self) -> SolverBindings:
        return SolverBindings(*self.solver_bindings)


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

    from eazy_sdk.core.http import ManagedCookieSetDescriptor

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
    remaining: int | None = None,
) -> None:
    if policy.max_replays <= 0:
        raise ReplayDeniedError("reaction replay is disabled")
    if remaining is not None and remaining <= 0:
        raise ReplayDeniedError("reaction replay budget of this policy is exhausted")
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


def solution_cookie_set(
    *,
    field: str = "cookies",
    name_field: str = "name",
    value_field: str = "value",
) -> PrivateCookieSetBinding:
    """Declare a dynamic, iterable cookie set selected from a solver result."""

    return PrivateCookieSetBinding(field, name_field, value_field)


@dataclass(frozen=True, slots=True)
class SolutionFields[TSolution]:
    """Declarative, compiler-reserved destinations for one complete solution batch."""

    bindings: PrivateBindings[TSolution]

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, PrivateBindings):
            raise ProtectionConfigurationError("solution fields require PrivateBindings")


def solution_fields[TSolution](
    *,
    headers: Mapping[str, str | None] | None = None,
    query: Mapping[str, str | None] | None = None,
    cookies: Mapping[str, str | None] | None = None,
    body: Mapping[str, str | None] | None = None,
    cookie_set: PrivateCookieSetBinding | None = None,
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
        if not isinstance(cookie_set, PrivateCookieSetBinding):
            raise ProtectionConfigurationError(
                "cookie_set must be created by solution_cookie_set()"
            )
        targets.append(cookie_set)
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
        # The callback is bound to one typed detector; ``model`` is informational only.
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
    policy: ChallengePolicy[TChallenge, TSolution]
    binding: SolverBinding[TChallenge, TSolution]

    def to_bundle(self) -> ProtectionBundle:
        return ProtectionBundle(
            challenge_policies=(self.policy,),
            solver_bindings=(self.binding,),
        )


type GuardCache = Literal["none", "call", "session"]
"""Cache policy of a simple guard.

``"none"``: every matching challenge is solved again and the solution is used for one
replay only. ``"call"``: the solution is reused for the remaining attempts of one logical
call. ``"session"``: the solution is shared by every call made through the same handler
session until the origin rejects it (or ``expires_at`` on the solution passes).
"""

type SolverLike[TChallenge, TSolution] = (
    ChallengeSolver[TChallenge, TSolution]
    | Callable[[TChallenge, SolveContext], TSolution | Awaitable[TSolution]]
)


def _cache_persistence(cache: GuardCache) -> ProtectionPersistence:
    if cache == "none":
        return per_match()
    if cache == "call":
        return per_call()
    if cache == "session":
        return until_rejected(scope=session_identity())
    raise ProtectionConfigurationError(
        f"challenge guard cache must be 'none', 'call' or 'session', not {cache!r}"
    )


@dataclass(frozen=True, slots=True, eq=False)
class _CallableSolver[TChallenge, TSolution]:
    """Adapt a plain function or a sync/async ``solve()`` method to the solver protocol."""

    target: Callable[[TChallenge, SolveContext], TSolution | Awaitable[TSolution]]

    async def solve(self, challenge: TChallenge, context: SolveContext) -> TSolution:
        result = self.target(challenge, context)
        if inspect.isawaitable(result):
            return cast(TSolution, await result)
        return result


def _adapt_solver[TChallenge, TSolution](
    solver: SolverLike[TChallenge, TSolution],
) -> ChallengeSolver[TChallenge, TSolution]:
    solve = getattr(solver, "solve", None)
    if callable(solve):
        return _CallableSolver(solve)
    if callable(solver):
        return _CallableSolver(cast(Any, solver))
    raise ProtectionConfigurationError(
        "challenge guard solver must define solve() or be a callable"
    )


def _detector_model[TChallenge](
    detect: Callable[[ResponseContext[object]], TChallenge | None],
) -> type[TChallenge]:
    """Best-effort challenge model from the detector's return annotation."""

    try:
        hints = get_type_hints(detect)
    except Exception:
        return cast(type[TChallenge], object)
    annotation = hints.get("return")
    candidates = (
        get_args(annotation)
        if get_origin(annotation) in {Union, UnionType}
        else (annotation,)
    )
    for candidate in candidates:
        if isinstance(candidate, type) and candidate is not type(None):
            return cast(type[TChallenge], candidate)
    return cast(type[TChallenge], object)


def _guard_name(detect: object) -> str:
    owner = getattr(detect, "__self__", None)
    if owner is not None and not inspect.isclass(owner) and not inspect.ismodule(owner):
        return type(owner).__name__
    name = getattr(detect, "__name__", None)
    if isinstance(name, str) and name and name != "<lambda>":
        return name
    raise ProtectionConfigurationError(
        "challenge guard name cannot be inferred from the detector; pass name="
    )


def challenge_guard[TChallenge, TSolution](
    *,
    detect: Callable[[ResponseContext[object]], TChallenge | None],
    solver: SolverLike[TChallenge, TSolution],
    apply: SolutionFields[TSolution],
    name: str | None = None,
    scope: RequestScope | None = None,
    cache: GuardCache = "none",
    replay: ReplayPolicy | None = None,
    revision: int = 1,
) -> InstallableProtection:
    """Lower one typed detector/solver/application guard into the shared executor.

    ``scope`` defaults to every request of the client. ``name`` defaults to the detector's
    function or owner class name. ``solver`` may be an object with ``solve()`` (sync or async)
    or a plain function ``(challenge, context) -> solution``. ``cache`` selects how long a
    solution is reused; see :data:`GuardCache`. Only cached modes share one in-flight solve
    between concurrent calls; with ``cache="none"`` concurrent challenges are solved
    independently.
    """

    if not callable(detect):
        raise ProtectionConfigurationError("challenge guard detector must be callable")
    if name is None:
        name = _guard_name(detect)
    if not name:
        raise ProtectionConfigurationError("challenge guard name must not be empty")
    if not isinstance(apply, SolutionFields):
        raise ProtectionConfigurationError(
            "challenge guard application must be created by solution_fields()"
        )
    selected_scope = scope if scope is not None else RequestScope()
    if not isinstance(selected_scope, RequestScope):
        raise ProtectionConfigurationError("challenge guard scope must be a RequestScope")
    adapted = _adapt_solver(solver)
    persistence = _cache_persistence(cache)
    requirement = SolverRequirement[TChallenge, TSolution](name)
    signal = ResponseSignal(
        f"{name}.response",
        selected_scope,
        _detector_model(detect),
        _SimpleDetectorParser(name, detect),
    )
    policy = challenge_policy(
        identity=name,
        revision=revision,
        scope=selected_scope,
        signal=signal,
        solver=requirement,
        apply=apply.bindings,
        persistence=persistence,
        replay=replay or safe_method(max_replays=1),
    )
    return _ChallengeGuard(policy, bind_solver(requirement, adapted))


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
    "AmbiguousChallengeError",
    "AmbiguousSignal",
    "BeforeCallPolicy",
    "BodyReplayPolicy",
    "ChallengeMalformedError",
    "ChallengePolicy",
    "FromProtection",
    "GuardCache",
    "MalformedSignal",
    "OperationReference",
    "PrivateBinding",
    "PrivateBindingTarget",
    "PrivateBindings",
    "PrivateCookieSetBinding",
    "ProtectedFetch",
    "ProtectionBundle",
    "ProtectionFlow",
    "ProtectionPersistence",
    "ProtectionPersistenceMode",
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
    "SolverLike",
    "SolverRequirement",
    "TransportIdentity",
    "before_call_policy",
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
