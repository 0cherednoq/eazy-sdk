from __future__ import annotations

import inspect
from typing import Annotated, Any, NotRequired, TypedDict, cast

import pytest

import eazy_sdk.request as request_api
from eazy_sdk import UNSET, Omittable, SyncApi, api
from eazy_sdk.core import (
    PlanError,
    PlanNodeKind,
    RequestLocation,
)
from eazy_sdk.request import BodyProjection
from eazy_sdk.request.markers import JsonBody, JsonField, Path, Query
from eazy_sdk.response import Responses

RESPONSES: Responses[object] = Responses(success=())


class PublicBody(TypedDict):
    value: str


class WireBody(TypedDict):
    nested: str


class UpdateBody(TypedDict):
    display_name: str
    timezone: str


class UpdateRequest(UpdateBody):
    user_id: Annotated[str, Path()]
    locale: NotRequired[Annotated[str, Query()]]


class UpdateWire(TypedDict):
    profile: dict[str, str]


class UnknownSource(TypedDict):
    value: str
    missing: str


class AnnotationMismatchRequest(TypedDict):
    value: int


class RequirednessMismatchRequest(TypedDict):
    value: NotRequired[str]


class ExtraRequest(PublicBody):
    outside: str


class PlacedRequest(TypedDict):
    value: Annotated[str, JsonField()]


class MixedRequest(PublicBody):
    extra: Annotated[str, JsonField()]


def to_wire(source: PublicBody) -> WireBody:
    return {"nested": source["value"]}


PROJECTION = BodyProjection(WireBody, to_wire, JsonBody(), source=PublicBody)


def _compile(api_type: type[SyncApi], method: str = "operation") -> Any:
    descriptor = cast(Any, getattr(api_type, method))
    return descriptor.resolve().compile()


def test_public_projection_compiles_unplaced_source_as_logical_slots() -> None:
    class ProjectionApi(SyncApi):
        @api.post(
            "/project",
            success=RESPONSES.success,
            errors=RESPONSES.errors,
            fallback=RESPONSES.fallback,
            projection=PROJECTION,
        )
        def operation(self, *, value: str) -> object:
            raise NotImplementedError

    descriptor = cast(Any, ProjectionApi.operation)
    compiled = _compile(ProjectionApi)

    assert request_api.BodyProjection is BodyProjection
    assert "BodyProjection" in request_api.__all__
    assert descriptor.declaration.input_schema.operation_type is descriptor.Operation
    assert compiled.body_projection is PROJECTION
    assert tuple(compiled.projection_slots) == ("value",)
    assert tuple(compiled.input_slots) == ("value",)
    assert compiled.body_slot is None
    assert compiled.body_slots == {}
    assert compiled.body_field_slots == {}
    assert compiled.input_fields[0].wire_name is None
    assert compiled.input_fields[0].location is None
    assert compiled.input_fields[0].is_projection_source
    assert tuple(descriptor.signature.parameters) == ("self", "value")
    phase_kinds = tuple(node.kind for node in compiled.plan.phases)
    assert phase_kinds.index(PlanNodeKind.BODY_PROJECTION) < phase_kinds.index(
        PlanNodeKind.PREPARE
    )


def test_projection_source_can_be_a_structural_subset_of_public_input() -> None:
    def update_to_wire(source: UpdateBody) -> UpdateWire:
        return {
            "profile": {
                "display_name": source["display_name"],
                "timezone": source["timezone"],
            }
        }

    projection = BodyProjection(UpdateWire, update_to_wire, JsonBody(), source=UpdateBody)

    class UpdateApi(SyncApi):
        @api.patch(
            "/users/{user_id}",
            success=RESPONSES.success,
            errors=RESPONSES.errors,
            fallback=RESPONSES.fallback,
            projection=projection,
        )
        def operation(
            self,
            *,
            display_name: str,
            timezone: str,
            user_id: Annotated[str, Path()],
            locale: Annotated[str | None, Query()] = None,
        ) -> object:
            raise NotImplementedError

    compiled = _compile(UpdateApi)

    assert tuple(compiled.projection_slots) == ("display_name", "timezone")
    assert tuple(compiled.path_slots) == ("user_id",)
    assert tuple(compiled.query_slots) == ("locale",)
    assert compiled.input_fields[2].location is RequestLocation.PATH


def test_projection_identity_contributes_to_plan_fingerprint() -> None:
    class First(SyncApi):
        @api.post(
            "/project",
            success=RESPONSES.success,
            errors=RESPONSES.errors,
            fallback=RESPONSES.fallback,
            projection=PROJECTION,
        )
        def operation(self, *, value: str) -> object:
            raise NotImplementedError

    named = BodyProjection(WireBody, to_wire, JsonBody(), name="named-v2", source=PublicBody)

    class Second(SyncApi):
        @api.post(
            "/project",
            success=RESPONSES.success,
            errors=RESPONSES.errors,
            fallback=RESPONSES.fallback,
            projection=named,
        )
        def operation(self, *, value: str) -> object:
            raise NotImplementedError

    assert _compile(First).plan.fingerprint != _compile(Second).plan.fingerprint
    assert PROJECTION.fingerprint_name.endswith(":to_wire")
    assert named.fingerprint_name == "named-v2"


def test_rejects_projection_source_that_is_not_a_typed_dict() -> None:
    invalid = BodyProjection(
        WireBody,
        cast(Any, lambda source: source),
        JsonBody(),
        source=cast(Any, dict),
    )
    with pytest.raises(PlanError, match=r"source.*not a model any configured adapter supports"):

        class InvalidApi(SyncApi):
            @api.post(
                "/project",
                success=RESPONSES.success,
                errors=RESPONSES.errors,
                fallback=RESPONSES.fallback,
                projection=invalid,
            )
            def operation(self, *, value: str) -> object:
                raise NotImplementedError


def test_rejects_projection_when_source_is_absent_from_direct_input() -> None:
    with pytest.raises(PlanError, match="not present in the public input"):

        class InvalidApi(SyncApi):
            @api.post(
                "/project",
                success=RESPONSES.success,
                errors=RESPONSES.errors,
                fallback=RESPONSES.fallback,
                projection=PROJECTION,
            )
            def operation(self) -> object:
                raise NotImplementedError


def test_rejects_unknown_source_key() -> None:
    projection = BodyProjection(
        WireBody,
        cast(Any, to_wire),
        JsonBody(),
        source=UnknownSource,
    )
    with pytest.raises(PlanError, match=r"source field 'missing'.*not present"):

        class InvalidApi(SyncApi):
            @api.post(
                "/project",
                success=RESPONSES.success,
                errors=RESPONSES.errors,
                fallback=RESPONSES.fallback,
                projection=projection,
            )
            def operation(self, *, value: str) -> object:
                raise NotImplementedError


def test_rejects_projection_source_shape_mismatch() -> None:
    projection = BodyProjection(
        WireBody,
        cast(Any, to_wire),
        JsonBody(),
        source=PublicBody,
    )

    with pytest.raises(PlanError, match="incompatible annotation"):

        class AnnotationMismatch(SyncApi):
            @api.post("/project", success=(), projection=projection)
            def operation(self, *, value: int) -> object:
                raise NotImplementedError

    with pytest.raises(PlanError, match="incompatible requiredness"):

        class RequirednessMismatch(SyncApi):
            @api.post("/project", success=(), projection=projection)
            def operation(self, *, value: Omittable[str] = UNSET) -> object:
                raise NotImplementedError


def test_rejects_unplaced_field_outside_projection_source() -> None:
    with pytest.raises(PlanError, match=r"outside.*no placement"):

        class InvalidApi(SyncApi):
            @api.post(
                "/project",
                success=RESPONSES.success,
                errors=RESPONSES.errors,
                fallback=RESPONSES.fallback,
                projection=PROJECTION,
            )
            def operation(self, *, value: str, outside: str) -> object:
                raise NotImplementedError


def test_rejects_projection_source_with_another_placement() -> None:
    with pytest.raises(
        PlanError,
        match=r"source field 'value'.*also declares a placement",
    ):

        class InvalidApi(SyncApi):
            @api.post(
                "/project",
                success=RESPONSES.success,
                errors=RESPONSES.errors,
                fallback=RESPONSES.fallback,
                projection=PROJECTION,
            )
            def operation(self, *, value: Annotated[str, JsonField()]) -> object:
                raise NotImplementedError


def test_rejects_projection_mixed_with_other_body_paths() -> None:
    with pytest.raises(PlanError, match="mixes a body projection"):

        class InvalidApi(SyncApi):
            @api.post(
                "/project",
                success=RESPONSES.success,
                errors=RESPONSES.errors,
                fallback=RESPONSES.fallback,
                projection=PROJECTION,
            )
            def operation(self, *, value: str, extra: Annotated[str, JsonField()]) -> object:
                raise NotImplementedError

    assert "wire_body" not in inspect.signature(api.post).parameters


def test_rejects_unsupported_projection_target_during_compile() -> None:
    class UnsupportedWire:
        pass

    projection = BodyProjection(
        UnsupportedWire,
        cast(Any, to_wire),
        JsonBody(),
        source=PublicBody,
    )

    class InvalidApi(SyncApi):
        @api.post(
            "/project",
            success=RESPONSES.success,
            errors=RESPONSES.errors,
            fallback=RESPONSES.fallback,
            projection=projection,
        )
        def operation(self, *, value: str) -> object:
            raise NotImplementedError

    with pytest.raises(PlanError, match=r"target.*unsupported"):
        _compile(InvalidApi)


def test_rejects_non_body_encoding_and_empty_name() -> None:
    with pytest.raises(TypeError, match="projection encoding"):
        BodyProjection(WireBody, to_wire, cast(Any, object()), source=PublicBody)
    with pytest.raises(ValueError, match="name must not be empty"):
        BodyProjection(WireBody, to_wire, JsonBody(), name="", source=PublicBody)
