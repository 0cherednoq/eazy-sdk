from __future__ import annotations

from dataclasses import dataclass

import pytest

from eazy_sdk.compile import (
    HTTP_COMPILER_KIND,
    CompiledContract,
    InputField,
    compile_endpoint,
)
from eazy_sdk.core import (
    Append,
    Bind,
    BindingError,
    BoundArguments,
    CompilerRegistry,
    GraphError,
    OperationShape,
    OperationValues,
    PatchError,
    PlanNode,
    PlanNodeKind,
    PythonTypeValidator,
    Remove,
    ReplaceAll,
    RequestLocation,
    RequestScope,
    ScopeContext,
    Set,
    SlotCardinality,
    StagedEffect,
    ValuePatch,
    ValueSlot,
    WriterConflictError,
    apply_patch_atomic,
    compile_plan,
)
from eazy_sdk.core.kernel import OperationIdentity
from eazy_sdk.request.params import Path, Query


def slot[T](
    name: str,
    annotation: object,
    *,
    cardinality: SlotCardinality = SlotCardinality.ONE,
    required: bool = False,
) -> ValueSlot[T]:
    return ValueSlot(
        name,
        PythonTypeValidator(annotation),
        cardinality=cardinality,
        required=required,
    )


def test_slots_use_identity_and_duplicate_identity_is_rejected() -> None:
    first: ValueSlot[str] = slot("tag", str)
    second: ValueSlot[str] = slot("tag", str)
    assert first is not second
    assert first != second
    OperationShape((first, second))
    with pytest.raises(BindingError, match="duplicate slot identity"):
        OperationShape((first, first))


def test_bound_arguments_validate_membership_duplicates_required_and_types() -> None:
    name: ValueSlot[str] = slot("name", str, required=True)
    foreign: ValueSlot[str] = slot("foreign", str)
    shape = OperationShape((name,))
    values = OperationValues.from_bound(shape, BoundArguments((Bind(name, "ok"),)))
    assert values.require(name) == "ok"
    with pytest.raises(BindingError, match="duplicate binding"):
        OperationValues.from_bound(shape, BoundArguments((Bind(name, "a"), Bind(name, "b"))))
    with pytest.raises(BindingError, match="does not belong"):
        OperationValues.from_bound(shape, BoundArguments((Bind(foreign, "x"),)))
    with pytest.raises(BindingError, match="required slot"):
        OperationValues.from_bound(shape, BoundArguments(()))
    with pytest.raises(TypeError, match="expected"):
        OperationValues.from_bound(shape, BoundArguments((Bind(name, 3),)))


def test_patch_replacement_and_groups_keep_shape_position() -> None:
    first: ValueSlot[str] = slot("first", str)
    challenge: ValueSlot[str] = slot("challenge", str)
    tags: ValueSlot[str] = slot("tag", str, cardinality=SlotCardinality.MANY)
    last: ValueSlot[str] = slot("last", str)
    shape = OperationShape((first, challenge, tags, last))
    values = OperationValues.from_bound(
        shape,
        BoundArguments(
            (Bind(first, "1"), Bind(challenge, "old"), Bind(tags, ("a", "b")), Bind(last, "9"))
        ),
    )
    changed = apply_patch_atomic(
        values,
        ValuePatch((Set(challenge, "new"), ReplaceAll(tags, ("x", "y")))),
    )
    changed = apply_patch_atomic(changed, ValuePatch((Append(tags, "z"),)))
    assert [(item[0].diagnostic_name, item[1]) for item in changed.ordered_items()] == [
        ("first", "1"),
        ("challenge", "new"),
        ("tag", ("x", "y", "z")),
        ("last", "9"),
    ]


def test_patch_batch_and_effects_are_atomic() -> None:
    first: ValueSlot[int] = slot("first", int)
    single: ValueSlot[str] = slot("single", str)
    shape = OperationShape((first, single))
    original = OperationValues.from_bound(
        shape, BoundArguments((Bind(first, 1), Bind(single, "x")))
    )
    committed: list[str] = []
    with pytest.raises(PatchError, match="append requires"):
        apply_patch_atomic(
            original,
            ValuePatch((Set(first, 2), Append(single, "bad"))),
            staged_effects=(StagedEffect(lambda: committed.append("committed")),),
        )
    assert original.require(first) == 1
    assert committed == []
    with pytest.raises(PatchError, match="cannot remove required"):
        required: ValueSlot[str] = slot("required", str, required=True)
        apply_patch_atomic(
            OperationValues.from_bound(
                OperationShape((required,)), BoundArguments((Bind(required, "x"),))
            ),
            ValuePatch((Remove(required),)),
        )


def test_plan_graph_detects_conflicts_back_edges_and_full_cycles() -> None:
    value: ValueSlot[str] = slot("value", str)
    shape = OperationShape((value,))
    writer_a = PlanNode("dependency-a", PlanNodeKind.DEPENDENCY, writes=(value,))
    writer_b = PlanNode("auth-b", PlanNodeKind.AUTH, writes=(value,))
    with pytest.raises(WriterConflictError, match=r"dependency-a.*auth-b"):
        compile_plan(
            operation=OperationIdentity("conflict"),
            shape=shape,
            nodes=(writer_a, writer_b),
            responses=object(),
        )
    late = PlanNode("late-sign", PlanNodeKind.SIGN)
    early = PlanNode("early-auth", PlanNodeKind.AUTH, after=(late,))
    with pytest.raises(GraphError, match="phase back-edge"):
        compile_plan(
            operation=OperationIdentity("back-edge"),
            shape=shape,
            nodes=(late, early),
            responses=object(),
        )
    first = PlanNode("first", PlanNodeKind.AUTH)
    second = PlanNode("second", PlanNodeKind.AUTH, after=(first,))
    object.__setattr__(first, "after", (second,))
    with pytest.raises(GraphError, match=r"first -> second -> first|second -> first -> second"):
        compile_plan(
            operation=OperationIdentity("cycle"),
            shape=shape,
            nodes=(first, second),
            responses=object(),
        )


def test_scope_round_trip_and_custom_scope_is_separate() -> None:
    operation = OperationIdentity("getPayment")
    scope = RequestScope(
        schemes=frozenset({"https"}),
        hosts=frozenset({"api.example"}),
        path_prefixes=("/v1/",),
        methods=frozenset({"GET"}),
        operation_ids=frozenset({"getPayment"}),
    )
    restored = RequestScope.from_dict(scope.to_dict())
    assert restored == scope
    assert restored.matches(ScopeContext("https", "api.example", "/v1/pay", "GET", operation))
    assert not restored.matches(ScopeContext("https", "other.example", "/v1/pay", "GET", operation))


@dataclass(frozen=True)
class Contract:
    operation_id: str = "getPayment"
    method: str = "GET"
    path: str = "/payments/{payment_id}"
    input_fields: tuple[InputField, ...] = (
        InputField(
            "payment_id",
            "payment_id",
            str,
            True,
            RequestLocation.PATH,
            Path("payment_id"),
        ),
        InputField(
            "expand",
            "expand",
            list[str],
            False,
            RequestLocation.QUERY,
            Query("expand", explode=False),
        ),
    )
    responses: object = "responses"


def test_compact_contract_compiles_and_binds_without_public_slots() -> None:
    hand_written: CompiledContract[str] = compile_endpoint(Contract())
    generated: CompiledContract[str] = compile_endpoint(
        Contract(), registry=CompilerRegistry(HTTP_COMPILER_KIND, revision=1)
    )
    assert hand_written.plan.shape.slots[0] is hand_written.path_slots["payment_id"]
    values = OperationValues.from_bound(
        hand_written.plan.shape,
        hand_written.bind_input({"payment_id": "pay_1", "expand": ["details"]}),
    )
    assert [item[1] for item in values.ordered_items()] == ["pay_1", ["details"]]
    assert hand_written.plan.fingerprint == generated.plan.fingerprint
    assert all("RequestDraft" not in repr(item) for item in hand_written.plan.phases)
