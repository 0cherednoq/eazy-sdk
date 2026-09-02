"""Immutable authoring model shared by vendor preset modules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from eazy_sdk.ext import RequestScope
from eazy_sdk.protection import (
    BodyAccess,
    ChallengeSolver,
    ChallengeSolverBinding,
    NetworkIdentityExpectation,
    PrivateBindings,
    ProtectionBundle,
    ProtectionCapabilities,
    ProtectionPersistence,
    ResponseSignal,
    SolverRequirement,
    bind_challenge_solver,
    client_identity,
    private_bindings,
    private_body,
    private_header,
    private_query,
)
from eazy_sdk.protection import (
    per_call as protection_per_call,
)
from eazy_sdk.protection import (
    per_match as protection_per_match,
)
from eazy_sdk.protection import (
    until_expiry as protection_until_expiry,
)


@dataclass(frozen=True, slots=True)
class PresetId:
    vendor: str
    name: str

    def __str__(self) -> str:
        return f"{self.vendor}.{self.name}"


@dataclass(frozen=True, slots=True)
class PresetChallengePolicy:
    id: PresetId
    identity: str
    revision: int
    scope: RequestScope
    signal: ResponseSignal[Any]
    solver: SolverRequirement[Any, Any]
    apply: PrivateBindings[Any]
    persistence: ProtectionPersistence
    replay: Any
    capabilities: ProtectionCapabilities
    challenge_identity: Any | None = None
    expected_identity: NetworkIdentityExpectation[Any] | None = None
    solver_binding: ChallengeSolverBinding[Any, Any] | None = None
    customized: frozenset[str] = frozenset()

    def to_bundle(self) -> ProtectionBundle:
        return ProtectionBundle(
            challenge_policies=(self,),
            challenge_solver_bindings=(
                (self.solver_binding,) if self.solver_binding is not None else ()
            ),
        )

    def replace_parser(self, parser: Any) -> PresetChallengePolicy:
        if not callable(getattr(parser, "bind", None)):
            raise TypeError("parser must implement bind(response)")
        return replace(
            self,
            signal=replace(self.signal, parser=parser),
            customized=self.customized | {"parser"},
        )

    def extend_detection(self, predicate: Any) -> PresetChallengePolicy:
        def combined(context: Any) -> bool:
            return (
                self.signal.prefilter is None or self.signal.prefilter(context)
            ) and bool(predicate(context))

        return replace(
            self,
            signal=replace(self.signal, prefilter=combined),
            customized=self.customized | {"detection"},
        )

    def replace_application(self, target: PrivateBindings[Any]) -> PresetChallengePolicy:
        return replace(
            self,
            apply=target,
            customized=self.customized | {"application"},
        )


@dataclass(frozen=True, slots=True)
class PresetBeforeCallPolicy:
    id: PresetId
    identity: str
    revision: int
    scope: RequestScope
    acquire: None
    challenge: object
    solver: SolverRequirement[Any, Any]
    apply: PrivateBindings[Any]
    persistence: ProtectionPersistence
    capabilities: ProtectionCapabilities
    expected_identity: NetworkIdentityExpectation[Any] | None = None
    solver_binding: ChallengeSolverBinding[Any, Any] | None = None
    customized: frozenset[str] = frozenset()

    def to_bundle(self) -> ProtectionBundle:
        return ProtectionBundle(
            before_call_policies=(self,),
            challenge_solver_bindings=(
                (self.solver_binding,) if self.solver_binding is not None else ()
            ),
        )

    def replace_application(self, target: PrivateBindings[Any]) -> PresetBeforeCallPolicy:
        return replace(
            self,
            apply=target,
            customized=self.customized | {"application"},
        )


@dataclass(frozen=True, slots=True)
class ProtectionTemplate[TChallenge, TSolution]:
    id: PresetId
    revision: int
    solver_requirement: SolverRequirement[TChallenge, TSolution]
    capabilities: ProtectionCapabilities

    def bind_challenge(
        self,
        *,
        scope: RequestScope,
        signal: ResponseSignal[TChallenge],
        apply: PrivateBindings[TSolution],
        persistence: ProtectionPersistence,
        replay: Any,
        challenge_identity: Any | None = None,
        expected_identity: NetworkIdentityExpectation[TSolution] | None = None,
        solver: ChallengeSolver[TChallenge, TSolution] | None = None,
    ) -> PresetChallengePolicy:
        return PresetChallengePolicy(
            id=self.id,
            identity=str(self.id),
            revision=self.revision,
            scope=scope,
            signal=signal,
            solver=self.solver_requirement,
            apply=apply,
            persistence=persistence,
            replay=replay,
            capabilities=self.capabilities,
            challenge_identity=challenge_identity,
            expected_identity=expected_identity,
            solver_binding=(
                bind_challenge_solver(
                    self.solver_requirement,
                    solver,
                )
                if solver is not None
                else None
            ),
        )

    def bind_before(
        self,
        *,
        scope: RequestScope,
        apply: PrivateBindings[TSolution],
        persistence: ProtectionPersistence,
        challenge: TChallenge,
        expected_identity: NetworkIdentityExpectation[TSolution] | None = None,
        solver: ChallengeSolver[TChallenge, TSolution] | None = None,
    ) -> PresetBeforeCallPolicy:
        return PresetBeforeCallPolicy(
            id=self.id,
            identity=str(self.id),
            revision=self.revision,
            scope=scope,
            acquire=None,
            challenge=challenge,
            solver=self.solver_requirement,
            apply=apply,
            persistence=persistence,
            capabilities=self.capabilities,
            expected_identity=expected_identity,
            solver_binding=(
                bind_challenge_solver(
                    self.solver_requirement,
                    solver,
                )
                if solver is not None
                else None
            ),
        )


def host(name: str) -> RequestScope:
    return RequestScope(hosts=frozenset({name}))


def operation(value: object) -> RequestScope:
    operation_id = getattr(value, "operation_id", value)
    if not isinstance(operation_id, str) or not operation_id:
        raise TypeError("operation scope requires an operation id or decorated API method")
    return RequestScope(operation_ids=frozenset({operation_id}))


def form_field(name: str, *, value_field: str | None = "token") -> PrivateBindings[Any]:
    return private_bindings(private_body(name, field=value_field))


def json_field(name: str, *, value_field: str | None = "token") -> PrivateBindings[Any]:
    return private_bindings(private_body(name, field=value_field))


def header(name: str, *, value_field: str | None = "token") -> PrivateBindings[Any]:
    return private_bindings(private_header(name, field=value_field))


def query(name: str, *, value_field: str | None = "token") -> PrivateBindings[Any]:
    return private_bindings(private_query(name, field=value_field))


def per_call() -> ProtectionPersistence:
    return protection_per_call()


def per_match() -> ProtectionPersistence:
    return protection_per_match()


def until_expiry() -> ProtectionPersistence:
    return protection_until_expiry(scope=client_identity())


__all__ = [
    "BodyAccess",
    "PresetBeforeCallPolicy",
    "PresetChallengePolicy",
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
