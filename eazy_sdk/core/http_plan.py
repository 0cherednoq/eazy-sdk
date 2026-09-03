"""Typed execution plan records and deterministic graph compiler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .kernel import (
    BoundArguments,
    OperationCallState,
    OperationIdentity,
    OperationShape,
    OperationValues,
    SourcePointer,
    ValueSlot,
    compile_graph,
)


class PlanNodeKind(IntEnum):
    BIND = 0
    DEPENDENCY = 10
    AUTH = 20
    CONTRIBUTE = 30
    BODY_PROJECTION = 33
    PRIVATE_WIRE = 34
    OUTBOUND_DOCUMENT_CRYPTO = 35
    PREPARE = 40
    OUTBOUND_ENCODED_CRYPTO = 45
    DERIVE = 50
    SIGN = 60
    RATE_LIMIT = 70
    EMIT = 80
    INBOUND_ENCODED_CRYPTO = 85
    INBOUND_DOCUMENT_CRYPTO = 87
    INSPECT = 90
    REACT = 100
    MATERIALIZE = 110


@dataclass(frozen=True, slots=True, eq=False)
class PlanNode:
    name: str
    kind: PlanNodeKind
    reads: tuple[ValueSlot[Any], ...] = ()
    writes: tuple[ValueSlot[Any], ...] = ()
    after: tuple[PlanNode, ...] = ()
    source: SourcePointer | None = None


@dataclass(frozen=True, slots=True)
class WireRequirement:
    dimension: str
    minimum: str


@dataclass(frozen=True, slots=True)
class WireRequirements:
    dimensions: tuple[WireRequirement, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledReplayPolicy:
    max_attempts: int = 1
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class ScopeContext:
    scheme: str
    host: str
    path: str
    method: str
    operation: OperationIdentity


@dataclass(frozen=True, slots=True)
class RequestScope:
    schemes: frozenset[str] = frozenset()
    hosts: frozenset[str] = frozenset()
    path_prefixes: tuple[str, ...] = ()
    methods: frozenset[str] = frozenset()
    operation_ids: frozenset[str] = frozenset()

    def matches(self, context: ScopeContext) -> bool:
        return (
            (not self.schemes or context.scheme in self.schemes)
            and (not self.hosts or context.host in self.hosts)
            and (not self.path_prefixes or context.path.startswith(self.path_prefixes))
            and (not self.methods or context.method.upper() in self.methods)
            and (not self.operation_ids or context.operation.operation_id in self.operation_ids)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemes": sorted(self.schemes),
            "hosts": sorted(self.hosts),
            "path_prefixes": list(self.path_prefixes),
            "methods": sorted(self.methods),
            "operation_ids": sorted(self.operation_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RequestScope:
        def strings(name: str) -> tuple[str, ...]:
            raw = value.get(name, ())
            if not isinstance(raw, list | tuple):
                raise TypeError(f"scope field {name!r} must be a sequence")
            if not all(isinstance(item, str) for item in raw):
                raise TypeError(f"scope field {name!r} must contain strings")
            return tuple(raw)

        return cls(
            schemes=frozenset(strings("schemes")),
            hosts=frozenset(strings("hosts")),
            path_prefixes=strings("path_prefixes"),
            methods=frozenset(strings("methods")),
            operation_ids=frozenset(strings("operation_ids")),
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlan[T]:
    operation: OperationIdentity
    shape: OperationShape
    phases: tuple[PlanNode, ...]
    responses: object
    scope: RequestScope
    requirements: WireRequirements
    replay: CompiledReplayPolicy
    fingerprint: str


@dataclass(slots=True)
class AttemptCache:
    values: dict[object, object] = field(default_factory=dict)


@dataclass(slots=True)
class AttemptBudgets:
    hard_remaining: int
    transport_remaining: int = 0
    auth_remaining: int = 0
    reaction_remaining: int = 0
    redirect_remaining: int = 0


@dataclass(slots=True)
class HttpCallState(OperationCallState[ExecutionPlan[object]]):
    budgets: AttemptBudgets


@dataclass(slots=True)
class HttpAttemptState:
    logical_call: HttpCallState
    number: int
    values: OperationValues
    selected_auth: tuple[object, ...]
    attempt_cache: AttemptCache


def compile_plan[T](
    *,
    operation: OperationIdentity,
    shape: OperationShape,
    nodes: tuple[PlanNode, ...],
    responses: object,
    scope: RequestScope | None = None,
    requirements: WireRequirements | None = None,
    replay: CompiledReplayPolicy | None = None,
    fingerprint_context: tuple[str, ...] = (),
    slot_fingerprint: tuple[tuple[object, ...], ...] | None = None,
) -> ExecutionPlan[T]:
    scope = scope or RequestScope()
    requirements = requirements or WireRequirements()
    replay = replay or CompiledReplayPolicy()
    ordered = compile_graph(nodes)
    payload = {
        "operation": operation.operation_id,
        "slots": slot_fingerprint
        or tuple(
            (slot.diagnostic_name, slot.required, slot.cardinality.value) for slot in shape.slots
        ),
        "nodes": [[node.name, node.kind.name] for node in ordered],
        "scope": scope.to_dict(),
        "requirements": [[item.dimension, item.minimum] for item in requirements.dimensions],
        "context": list(fingerprint_context),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ExecutionPlan(
        operation=operation,
        shape=shape,
        phases=ordered,
        responses=responses,
        scope=scope,
        requirements=requirements,
        replay=replay,
        fingerprint=fingerprint,
    )


def bind_plan(plan: ExecutionPlan[object], arguments: BoundArguments) -> OperationValues:
    return OperationValues.from_bound(plan.shape, arguments)
