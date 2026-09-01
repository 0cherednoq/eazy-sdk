"""Protocol-neutral operation values, graph, scope and parsing primitives."""

from __future__ import annotations

import types
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Protocol, Self, Union, cast, get_args, get_origin, is_typeddict

from .errors import (
    BindingError,
    GraphError,
    PatchError,
    SlotBindingError,
    SlotValueError,
    WriterConflictError,
)


class SlotCardinality(Enum):
    ONE = "one"
    MANY = "many"


class PatchConflict(Enum):
    ERROR = "error"
    REPLACE_LOWER_PRIORITY = "replace_lower_priority"
    PRESERVE_EXISTING = "preserve_existing"


class ValueValidator[T](Protocol):
    def __call__(self, value: object) -> T: ...


@dataclass(frozen=True, slots=True)
class PythonTypeValidator[T]:
    annotation: object

    def __call__(self, value: object) -> T:
        validate_annotation(value, self.annotation)
        return value  # type: ignore[return-value]


@dataclass(frozen=True, slots=True, eq=False)
class ValueSlot[T]:
    diagnostic_name: str
    validator: ValueValidator[T]
    required: bool = False
    secret: bool = False
    cardinality: SlotCardinality = SlotCardinality.ONE

    def __repr__(self) -> str:
        flags = []
        if self.required:
            flags.append("required")
        if self.secret:
            flags.append("secret")
        suffix = f", {', '.join(flags)}" if flags else ""
        return f"ValueSlot({self.diagnostic_name!r}{suffix})"


_MISSING = object()


@dataclass(frozen=True, slots=True)
class OperationShape:
    slots: tuple[ValueSlot[Any], ...]

    def __post_init__(self) -> None:
        seen: set[int] = set()
        for slot in self.slots:
            identity = id(slot)
            if identity in seen:
                raise BindingError(f"duplicate slot identity in shape: {slot.diagnostic_name}")
            seen.add(identity)

    def index(self, target: ValueSlot[Any]) -> int:
        for index, slot in enumerate(self.slots):
            if slot is target:
                return index
        raise BindingError(f"slot does not belong to shape: {target.diagnostic_name}")


@dataclass(frozen=True, slots=True)
class Bind[T]:
    slot: ValueSlot[T]
    value: T


@dataclass(frozen=True, slots=True)
class BoundArguments:
    bindings: tuple[Bind[Any], ...]


@dataclass(frozen=True, slots=True)
class OperationValues:
    shape: OperationShape
    _values: tuple[object, ...]

    def __post_init__(self) -> None:
        if len(self._values) != len(self.shape.slots):
            raise BindingError("request values length does not match request shape")

    @classmethod
    def empty(cls, shape: OperationShape) -> OperationValues:
        return cls(shape, (_MISSING,) * len(shape.slots))

    @classmethod
    def from_bound(cls, shape: OperationShape, arguments: BoundArguments) -> OperationValues:
        values = [_MISSING] * len(shape.slots)
        occupied: set[int] = set()
        for binding in arguments.bindings:
            slot = binding.slot
            index = shape.index(slot)
            if index in occupied:
                raise BindingError(f"duplicate binding for slot: {slot.diagnostic_name}")
            occupied.add(index)
            if slot.cardinality is SlotCardinality.MANY:
                if not isinstance(binding.value, Sequence) or isinstance(
                    binding.value, str | bytes | bytearray
                ):
                    raise SlotValueError(
                        f"expected a sequence for multi-value slot {slot.diagnostic_name!r}",
                        slot=slot,
                    )
                try:
                    values[index] = tuple(slot.validator(item) for item in binding.value)
                except (TypeError, ValueError) as exc:
                    raise SlotValueError(
                        str(exc),
                        slot=slot,
                        path=_validation_error_path(exc),
                    ) from None
            else:
                try:
                    values[index] = slot.validator(binding.value)
                except (TypeError, ValueError) as exc:
                    raise SlotValueError(
                        str(exc),
                        slot=slot,
                        path=_validation_error_path(exc),
                    ) from None
        for index, slot in enumerate(shape.slots):
            if slot.required and values[index] is _MISSING:
                raise SlotBindingError(
                    "required slot has no value",
                    slot=slot,
                    reason="missing_required",
                )
        return cls(shape, tuple(values))

    def contains(self, slot: ValueSlot[object]) -> bool:
        return self._values[self.shape.index(slot)] is not _MISSING

    def get[T](self, slot: ValueSlot[T], default: T | None = None) -> T | None:
        value = self._values[self.shape.index(slot)]
        return default if value is _MISSING else value  # type: ignore[return-value]

    def require[T](self, slot: ValueSlot[T]) -> T:
        value = self._values[self.shape.index(slot)]
        if value is _MISSING:
            raise BindingError(f"slot has no value: {slot.diagnostic_name}")
        return value  # type: ignore[return-value]

    def ordered_items(self) -> tuple[tuple[ValueSlot[Any], object], ...]:
        return tuple(
            (slot, value)
            for slot, value in zip(self.shape.slots, self._values, strict=True)
            if value is not _MISSING
        )


@dataclass(frozen=True, slots=True)
class Set[T]:
    slot: ValueSlot[T]
    value: T
    conflict: PatchConflict = PatchConflict.ERROR


@dataclass(frozen=True, slots=True)
class Append[T]:
    slot: ValueSlot[T]
    value: T
    conflict: PatchConflict = PatchConflict.ERROR


@dataclass(frozen=True, slots=True)
class ReplaceAll[T]:
    slot: ValueSlot[T]
    values: Sequence[T]
    conflict: PatchConflict = PatchConflict.ERROR


@dataclass(frozen=True, slots=True)
class Remove:
    slot: ValueSlot[Any]
    conflict: PatchConflict = PatchConflict.ERROR


type PatchOperation = Set[Any] | Append[Any] | ReplaceAll[Any] | Remove


@dataclass(frozen=True, slots=True)
class ValuePatch:
    operations: tuple[PatchOperation, ...]


@dataclass(frozen=True, slots=True)
class StagedEffect:
    commit: Callable[[], None]


@dataclass(frozen=True, slots=True)
class SourcePointer:
    document: str
    pointer: str


@dataclass(frozen=True, slots=True, eq=False)
class OperationIdentity:
    operation_id: str
    source: SourcePointer | None = None


@dataclass(frozen=True, slots=True)
class OperationMetadata:
    tags: tuple[str, ...] = ()
    attributes: tuple[tuple[str, object], ...] = ()


@dataclass(slots=True)
class CallCache:
    values: dict[object, object] = field(default_factory=dict)


@dataclass(slots=True)
class OperationCallState[TPlan]:
    plan: TPlan
    bound_values: OperationValues
    call_cache: CallCache


class Scope[TContext](Protocol):
    def matches(self, context: TContext) -> bool: ...


@dataclass(frozen=True, slots=True)
class CustomScope[TContext]:
    predicate: Callable[[TContext], bool]
    diagnostic_name: str

    def __post_init__(self) -> None:
        if not self.diagnostic_name:
            raise ValueError("custom scope requires a diagnostic name")

    def matches(self, context: TContext) -> bool:
        return self.predicate(context)


@dataclass(frozen=True, slots=True)
class ParsedValue[T]:
    value: T


@dataclass(frozen=True, slots=True)
class NoMatch:
    pass


@dataclass(frozen=True, slots=True)
class Malformed:
    cause: Exception
    details: object | None = None


type ParseAttempt[T] = ParsedValue[T] | NoMatch | Malformed


@dataclass(frozen=True, slots=True)
class SelectedCase[TCase, TValue]:
    case: TCase
    value: TValue


@dataclass(frozen=True, slots=True)
class AmbiguousCases[TCase]:
    cases: tuple[TCase, ...]


@dataclass(frozen=True, slots=True)
class MalformedCase[TCase]:
    case: TCase
    malformed: Malformed


@dataclass(frozen=True, slots=True)
class NoCaseMatch:
    pass


type CaseArbitration[TCase, TValue] = (
    SelectedCase[TCase, TValue] | AmbiguousCases[TCase] | MalformedCase[TCase] | NoCaseMatch
)


def arbitrate_cases[TCase, TValue](
    matches: Sequence[tuple[TCase, TValue]],
    malformed: Sequence[tuple[TCase, Malformed]],
) -> CaseArbitration[TCase, TValue]:
    """Select one parsed case without depending on an HTTP response or WS message."""

    if len(matches) > 1:
        return AmbiguousCases(tuple(case for case, _ in matches))
    if matches:
        case, value = matches[0]
        return SelectedCase(case, value)
    if malformed:
        case, failure = malformed[0]
        return MalformedCase(case, failure)
    return NoCaseMatch()


@dataclass(frozen=True, slots=True, eq=False)
class CompilerKind[TDeclaration]:
    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("compiler kind requires a name")


@dataclass(frozen=True, slots=True)
class CompilerRegistry[TDeclaration, TContribution]:
    kind: CompilerKind[TDeclaration]
    nodes: tuple[TContribution, ...] = ()
    revision: int = 0


class GraphNode(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> IntEnum: ...

    @property
    def reads(self) -> tuple[ValueSlot[Any], ...]: ...

    @property
    def writes(self) -> tuple[ValueSlot[Any], ...]: ...

    @property
    def after(self) -> tuple[Self, ...]: ...


def compile_graph[TNode: GraphNode](
    nodes: tuple[TNode, ...],
) -> tuple[TNode, ...]:
    """Compile a deterministic identity graph without knowing its wire protocol."""

    node_ids = {id(node) for node in nodes}
    if len(node_ids) != len(nodes):
        raise GraphError("duplicate plan node identity")
    writers: dict[int, TNode] = {}
    producer: dict[int, TNode] = {}
    edges: dict[int, set[int]] = defaultdict(set)
    by_id = {id(node): node for node in nodes}
    for node in nodes:
        for dependency in node.after:
            if id(dependency) not in node_ids:
                raise GraphError(f"node {node.name!r} depends on an unknown node")
            if dependency.kind > node.kind:
                raise GraphError(
                    f"phase back-edge: {dependency.name}({dependency.kind.name}) -> "
                    f"{node.name}({node.kind.name})"
                )
            edges[id(dependency)].add(id(node))
        for slot in node.writes:
            identity = id(slot)
            if identity in writers:
                previous = writers[identity]
                raise WriterConflictError(
                    f"slot {slot.diagnostic_name!r} has writers {previous.name!r} and {node.name!r}"
                )
            writers[identity] = node
            producer[identity] = node
    for node in nodes:
        for slot in node.reads:
            source = producer.get(id(slot))
            if source is not None and source is not node:
                if source.kind > node.kind:
                    raise GraphError(
                        f"phase back-edge: {source.name}({source.kind.name}) -> "
                        f"{node.name}({node.kind.name})"
                    )
                edges[id(source)].add(id(node))
    indegree = {id(node): 0 for node in nodes}
    for targets in edges.values():
        for target in targets:
            indegree[target] += 1
    ready = sorted(
        (node for node in nodes if indegree[id(node)] == 0),
        key=lambda node: (node.kind, node.name),
    )
    result: list[TNode] = []
    while ready:
        node = ready.pop(0)
        result.append(node)
        for target in sorted(edges[id(node)], key=lambda item: by_id[item].name):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(by_id[target])
                ready.sort(key=lambda item: (item.kind, item.name))
    if len(result) != len(nodes):
        cycle = _cycle_path(nodes, edges)
        raise GraphError("plan graph cycle: " + " -> ".join(cycle))
    return tuple(result)


def _cycle_path[TNode: GraphNode](
    nodes: tuple[TNode, ...], edges: dict[int, set[int]]
) -> list[str]:
    by_id = {id(node): node for node in nodes}
    visited: set[int] = set()
    active: list[int] = []

    def visit(identity: int) -> list[str] | None:
        if identity in active:
            start = active.index(identity)
            return [by_id[item].name for item in (*active[start:], identity)]
        if identity in visited:
            return None
        visited.add(identity)
        active.append(identity)
        for target in edges[identity]:
            found = visit(target)
            if found is not None:
                return found
        active.pop()
        return None

    for node in nodes:
        found = visit(id(node))
        if found is not None:
            return found
    return ["unknown"]


def apply_patch_atomic(
    values: OperationValues,
    patch: ValuePatch,
    *,
    staged_effects: tuple[StagedEffect, ...] = (),
) -> OperationValues:
    """Validate a complete patch before committing values or persistent effects."""
    candidate = list(values._values)
    writes: dict[int, PatchConflict] = {}
    for operation in patch.operations:
        slot = operation.slot
        try:
            index = values.shape.index(slot)
        except BindingError as exc:
            raise PatchError(str(exc)) from exc
        if index in writes and operation.conflict is PatchConflict.ERROR:
            raise PatchError(f"multiple writes in one patch: {slot.diagnostic_name}")
        writes[index] = operation.conflict
        if (
            operation.conflict is PatchConflict.PRESERVE_EXISTING
            and candidate[index] is not _MISSING
        ):
            continue
        if isinstance(operation, Remove):
            if slot.required:
                raise PatchError(f"cannot remove required slot: {slot.diagnostic_name}")
            candidate[index] = _MISSING
        elif isinstance(operation, Append):
            if slot.cardinality is not SlotCardinality.MANY:
                raise PatchError(f"append requires a multi-value slot: {slot.diagnostic_name}")
            item = slot.validator(operation.value)
            previous = candidate[index]
            if previous is _MISSING:
                candidate[index] = (item,)
            elif isinstance(previous, tuple):
                candidate[index] = (*previous, item)
            else:
                raise PatchError(f"invalid stored group for slot: {slot.diagnostic_name}")
        elif isinstance(operation, ReplaceAll):
            if slot.cardinality is not SlotCardinality.MANY:
                raise PatchError(f"replace-all requires a multi-value slot: {slot.diagnostic_name}")
            candidate[index] = tuple(slot.validator(item) for item in operation.values)
        else:
            candidate[index] = slot.validator(operation.value)
    result = OperationValues(values.shape, tuple(candidate))
    for effect in staged_effects:
        effect.commit()
    return result


def validate_annotation(value: object, annotation: object) -> None:
    if annotation in {Any, object}:
        return
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {Union, types.UnionType}:
        if any(_matches(value, option) for option in args):
            return
        raise TypeError(f"expected {annotation!r}, got {type(value).__name__}")
    if origin is not None:
        if not isinstance(value, origin):
            raise TypeError(f"expected {annotation!r}, got {type(value).__name__}")
        if isinstance(value, Mapping) and len(args) == 2:
            for key, item in value.items():
                validate_annotation(key, args[0])
                validate_annotation(item, args[1])
        elif (
            isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray) and args
        ):
            for item in value:
                validate_annotation(item, args[0])
        return
    if is_typeddict(annotation):
        from eazy_sdk.models import ModelAdapterError, default_model_adapters

        typed_dict = cast(type[object], annotation)
        if not isinstance(value, Mapping):
            raise TypeError(f"expected mapping for {typed_dict.__name__}")
        fields = default_model_adapters().fields(typed_dict)
        known = {field.name for field in fields}
        unknown = set(value) - known
        if unknown:
            raise ModelAdapterError(
                f"unknown fields for {typed_dict.__name__}: {sorted(unknown)!r}"
            )
        for field in fields:
            if field.name in value:
                validate_annotation(value[field.name], field.annotation)
            elif field.required:
                raise ModelAdapterError(
                    f"missing required field {typed_dict.__name__}.{field.name}"
                )
        return
    if isinstance(annotation, type) and isinstance(value, annotation):
        return
    if isinstance(annotation, type):
        from eazy_sdk.models import UnsupportedModelTypeError, default_model_adapters

        try:
            registry = default_model_adapters()
            registry.adapter_for_type(annotation)
            registry.load(annotation, value)
        except UnsupportedModelTypeError:
            pass
        else:
            return
    raise TypeError(f"expected {annotation!r}, got {type(value).__name__}")


def _validation_error_path(error: Exception) -> str | None:
    message = str(error)
    marker = "missing required field "
    if marker not in message:
        return None
    path = message.partition(marker)[2]
    _, separator, nested = path.partition(".")
    return nested if separator else path


def _matches(value: object, annotation: object) -> bool:
    try:
        validate_annotation(value, annotation)
    except (TypeError, ValueError):
        return False
    return True
