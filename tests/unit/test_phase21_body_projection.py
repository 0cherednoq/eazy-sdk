from __future__ import annotations

import inspect
from typing import Annotated, Any, NotRequired, TypedDict, Unpack, cast

import pytest

import eazy_sdk.request as request_api
from eazy_sdk import ApiDefaults, SyncApi, api
from eazy_sdk._internal import PlanError, PlanNodeKind, RequestLocation
from eazy_sdk.request import BodyProjection, JsonBody, JsonField, Path, Query
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


PROJECTION = BodyProjection(PublicBody, WireBody, to_wire, JsonBody())


def _compile(api_type: type[SyncApi], method: str = "operation") -> Any:
    descriptor = cast(Any, getattr(api_type, method))
    return descriptor.resolve(ApiDefaults()).compile()


def test_public_projection_compiles_unplaced_source_as_logical_slots() -> None:
    class ProjectionApi(SyncApi):
        @api.post("/project", body=PROJECTION, responses=RESPONSES)
        def operation(self, **request: Unpack[PublicBody]) -> object:
            raise NotImplementedError

    descriptor = cast(Any, ProjectionApi.operation)
    compiled = _compile(ProjectionApi)

    assert request_api.BodyProjection is BodyProjection
    assert "BodyProjection" in request_api.__all__
    assert descriptor.declaration.input_schema.unpacked is PublicBody
    assert compiled.body_projection is PROJECTION
    assert tuple(compiled.projection_slots) == ("value",)
    assert tuple(compiled.input_slots) == ("value",)
    assert compiled.body_slot is None
    assert compiled.body_slots == {}
    assert compiled.body_field_slots == {}
    assert compiled.input_fields[0].wire_name is None
    assert compiled.input_fields[0].location is None
    assert compiled.input_fields[0].is_projection_source
    assert tuple(descriptor.signature.parameters) == ("self", "request")
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

    projection = BodyProjection(UpdateBody, UpdateWire, update_to_wire, JsonBody())

    class UpdateApi(SyncApi):
        @api.patch("/users/{user_id}", body=projection, responses=RESPONSES)
        def operation(self, **request: Unpack[UpdateRequest]) -> object:
            raise NotImplementedError

    compiled = _compile(UpdateApi)

    assert tuple(compiled.projection_slots) == ("display_name", "timezone")
    assert tuple(compiled.path_slots) == ("user_id",)
    assert tuple(compiled.query_slots) == ("locale",)
    assert compiled.input_fields[2].location is RequestLocation.PATH


def test_projection_identity_contributes_to_plan_fingerprint() -> None:
    class First(SyncApi):
        @api.post("/project", body=PROJECTION, responses=RESPONSES)
        def operation(self, **request: Unpack[PublicBody]) -> object:
            raise NotImplementedError

    named = BodyProjection(PublicBody, WireBody, to_wire, JsonBody(), "named-v2")

    class Second(SyncApi):
        @api.post("/project", body=named, responses=RESPONSES)
        def operation(self, **request: Unpack[PublicBody]) -> object:
            raise NotImplementedError

    assert _compile(First).plan.fingerprint != _compile(Second).plan.fingerprint
    assert PROJECTION.fingerprint_name.endswith(":to_wire")
    assert named.fingerprint_name == "named-v2"


def test_rejects_projection_source_that_is_not_a_typed_dict() -> None:
    invalid = BodyProjection(
        cast(Any, dict),
        WireBody,
        cast(Any, lambda source: source),
        JsonBody(),
    )
    with pytest.raises(PlanError, match=r"source.*must be a TypedDict"):

        class InvalidApi(SyncApi):
            @api.post("/project", body=invalid, responses=RESPONSES)
            def operation(self, **request: Unpack[PublicBody]) -> object:
                raise NotImplementedError


def test_rejects_projection_when_source_is_absent_from_direct_input() -> None:
    with pytest.raises(PlanError, match="not present in the public input"):

        class InvalidApi(SyncApi):
            @api.post("/project", body=PROJECTION, responses=RESPONSES)
            def operation(self) -> object:
                raise NotImplementedError


def test_rejects_unknown_source_key() -> None:
    projection = BodyProjection(
        UnknownSource,
        WireBody,
        cast(Any, to_wire),
        JsonBody(),
    )
    with pytest.raises(PlanError, match=r"source field 'missing'.*not present"):

        class InvalidApi(SyncApi):
            @api.post("/project", body=projection, responses=RESPONSES)
            def operation(self, **request: Unpack[PublicBody]) -> object:
                raise NotImplementedError


@pytest.mark.parametrize(
    ("public_request", "mismatch"),
    [
        (AnnotationMismatchRequest, "annotation"),
        (RequirednessMismatchRequest, "requiredness"),
    ],
)
def test_rejects_projection_source_shape_mismatch(
    public_request: type[object], mismatch: str
) -> None:
    projection = BodyProjection(
        PublicBody,
        WireBody,
        cast(Any, to_wire),
        JsonBody(),
    )
    request_annotation = Unpack[public_request]

    with pytest.raises(PlanError, match=f"incompatible {mismatch}"):
        def operation(self: object, **request: object) -> object:
            raise NotImplementedError

        declaration = cast(Any, operation)
        declaration.__annotations__ = {"request": request_annotation, "return": object}
        api.post("/project", body=projection, responses=RESPONSES)(declaration)


def test_rejects_unplaced_field_outside_projection_source() -> None:
    with pytest.raises(PlanError, match=r"outside.*no placement"):

        class InvalidApi(SyncApi):
            @api.post("/project", body=PROJECTION, responses=RESPONSES)
            def operation(self, **request: Unpack[ExtraRequest]) -> object:
                raise NotImplementedError


def test_rejects_projection_source_with_another_placement() -> None:
    with pytest.raises(
        PlanError,
        match=r"source field 'value'.*also declares a placement",
    ):

        class InvalidApi(SyncApi):
            @api.post("/project", body=PROJECTION, responses=RESPONSES)
            def operation(self, **request: Unpack[PlacedRequest]) -> object:
                raise NotImplementedError


def test_rejects_projection_mixed_with_other_body_paths() -> None:
    with pytest.raises(PlanError, match="mixes a body projection"):

        class InvalidApi(SyncApi):
            @api.post("/project", body=PROJECTION, responses=RESPONSES)
            def operation(self, **request: Unpack[MixedRequest]) -> object:
                raise NotImplementedError

    assert "wire_body" not in inspect.signature(api.post).parameters


def test_rejects_unsupported_projection_target_during_compile() -> None:
    class UnsupportedWire:
        pass

    projection = BodyProjection(
        PublicBody,
        UnsupportedWire,
        cast(Any, to_wire),
        JsonBody(),
    )

    class InvalidApi(SyncApi):
        @api.post("/project", body=projection, responses=RESPONSES)
        def operation(self, **request: Unpack[PublicBody]) -> object:
            raise NotImplementedError

    with pytest.raises(PlanError, match=r"target.*unsupported"):
        _compile(InvalidApi)


def test_rejects_non_body_encoding_and_empty_name() -> None:
    with pytest.raises(TypeError, match="projection encoding"):
        BodyProjection(PublicBody, WireBody, to_wire, cast(Any, object()))
    with pytest.raises(ValueError, match="name must not be empty"):
        BodyProjection(PublicBody, WireBody, to_wire, JsonBody(), "")
