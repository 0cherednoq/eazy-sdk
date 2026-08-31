"""Immutable authoring model shared by vendor preset modules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from eazy_sdk.ext import RequestScope
from eazy_sdk.protection import (
    BeforeCall,
    ChallengeSolver,
    ResponseReaction,
    ResponseSignal,
    SolutionFreshness,
    SolutionTarget,
    SolverRegistry,
    SolverRequirement,
    body_field,
    header_target,
    query_target,
)


@dataclass(frozen=True, slots=True)
class PresetId:
    vendor: str
    name: str

    def __str__(self) -> str:
        return f"{self.vendor}.{self.name}"


class BodyAccess(Enum):
    NONE = "none"
    BUFFERED = "buffered"


@dataclass(frozen=True, slots=True)
class ProtectionCapabilities:
    response_body: BodyAccess = BodyAccess.NONE
    cookie_jar: bool = False
    javascript: bool = False
    browser: bool = False
    sticky_network_identity: bool = False


@dataclass(frozen=True, slots=True)
class BoundProtection:
    id: PresetId
    revision: int
    scope: RequestScope
    solver_requirement: SolverRequirement[Any, Any]
    capabilities: ProtectionCapabilities
    signals: tuple[ResponseSignal[Any], ...] = ()
    reactions: tuple[ResponseReaction[Any, Any], ...] = ()
    before: tuple[BeforeCall, ...] = ()
    solver: ChallengeSolver[Any, Any] | None = None
    customized: frozenset[str] = frozenset()

    def install(self, registry: SolverRegistry) -> None:
        if self.solver is not None:
            registry.register(self.solver_requirement, self.solver)

    def replace_parser(self, parser: Any) -> BoundProtection:
        if not callable(getattr(parser, "bind", None)):
            raise TypeError("parser must implement bind(response)")
        signals = tuple(replace(signal, parser=parser) for signal in self.signals)
        return replace(self, signals=signals, customized=self.customized | {"parser"})

    def extend_detection(self, predicate: Any) -> BoundProtection:
        def combined(context: Any) -> bool:
            return all(
                signal.prefilter is None or signal.prefilter(context) for signal in self.signals
            ) and bool(predicate(context))

        signals = tuple(replace(signal, prefilter=combined) for signal in self.signals)
        return replace(self, signals=signals, customized=self.customized | {"detection"})

    def replace_application(self, target: SolutionTarget) -> BoundProtection:
        reactions = tuple(replace(reaction, apply=target) for reaction in self.reactions)
        before = tuple(replace(flow, apply=target) for flow in self.before)
        return replace(
            self,
            reactions=reactions,
            before=before,
            customized=self.customized | {"application"},
        )


@dataclass(frozen=True, slots=True)
class ProtectionTemplate[TChallenge, TSolution]:
    id: PresetId
    revision: int
    solver_requirement: SolverRequirement[TChallenge, TSolution]
    capabilities: ProtectionCapabilities

    def bind(
        self,
        *,
        scope: RequestScope,
        solver: ChallengeSolver[TChallenge, TSolution] | None = None,
        signals: tuple[ResponseSignal[Any], ...] = (),
        reactions: tuple[ResponseReaction[Any, Any], ...] = (),
        before: tuple[BeforeCall, ...] = (),
    ) -> BoundProtection:
        return BoundProtection(
            self.id,
            self.revision,
            scope,
            self.solver_requirement,
            self.capabilities,
            signals,
            reactions,
            before,
            solver,
        )


def host(name: str) -> RequestScope:
    return RequestScope(hosts=frozenset({name}))


def operation(value: object) -> RequestScope:
    operation_id = getattr(value, "operation_id", value)
    if not isinstance(operation_id, str) or not operation_id:
        raise TypeError("operation scope requires an operation id or decorated API method")
    return RequestScope(operation_ids=frozenset({operation_id}))


def form_field(name: str, *, value_field: str | None = "token") -> SolutionTarget:
    return body_field(name, value_field=value_field)


def json_field(name: str, *, value_field: str | None = "token") -> SolutionTarget:
    return body_field(name, value_field=value_field)


def header(name: str, *, value_field: str | None = "token") -> SolutionTarget:
    return header_target(name, value_field=value_field)


def query(name: str, *, value_field: str | None = "token") -> SolutionTarget:
    return query_target(name, value_field=value_field)


def per_call() -> SolutionFreshness:
    return SolutionFreshness.PER_CALL


def per_match() -> SolutionFreshness:
    return SolutionFreshness.PER_MATCH


def until_expiry() -> SolutionFreshness:
    return SolutionFreshness.UNTIL_EXPIRY


__all__ = [
    "BodyAccess",
    "BoundProtection",
    "PresetId",
    "ProtectionCapabilities",
    "ProtectionTemplate",
    "form_field",
    "header",
    "host",
    "json_field",
    "operation",
    "per_call",
    "per_match",
    "query",
    "until_expiry",
]
