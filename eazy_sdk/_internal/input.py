"""Compile declarative API method signatures into request field metadata."""

from __future__ import annotations

import inspect
import re
import types
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import (
    Annotated,
    Union,
    Unpack,
    cast,
    get_args,
    get_origin,
    is_typeddict,
)

from eazy_sdk.codecs import BodyCodec
from eazy_sdk.models.adapters import TypedDictModelAdapter
from eazy_sdk.request.descriptors import (
    BodyProjection,
    BytesBody,
    Form,
    FormBody,
    JsonBody,
    JsonField,
    MultipartBody,
    Part,
    ReplayableStreamBody,
)
from eazy_sdk.request.params import Cookie, Header, Path, Query, QueryString

from .errors import PlanError
from .http import RequestLocation

type Placement = (
    Query
    | QueryString
    | Path
    | Header
    | Cookie
    | JsonField
    | Form
    | Part
    | JsonBody
    | FormBody
    | MultipartBody
    | BytesBody
    | ReplayableStreamBody
    | BodyCodec
)

_PLACEMENT_TYPES = (
    Query,
    QueryString,
    Path,
    Header,
    Cookie,
    JsonField,
    Form,
    Part,
    JsonBody,
    FormBody,
    MultipartBody,
    BytesBody,
    ReplayableStreamBody,
    BodyCodec,
)
_BODY_FIELD_TYPES = (JsonField, Form, Part)
_ROOT_BODY_TYPES = (
    JsonBody,
    FormBody,
    MultipartBody,
    BytesBody,
    ReplayableStreamBody,
    BodyCodec,
)
_PATH_EXPRESSION = re.compile(r"\{([^{}]+)\}")


@dataclass(frozen=True, slots=True)
class InputField:
    python_name: str
    wire_name: str | None
    annotation: object
    required: bool
    location: RequestLocation | None
    placement: Placement | None

    @property
    def is_body_field(self) -> bool:
        return isinstance(self.placement, _BODY_FIELD_TYPES)

    @property
    def is_root_body(self) -> bool:
        return isinstance(self.placement, _ROOT_BODY_TYPES)

    @property
    def is_projection_source(self) -> bool:
        return self.placement is None


@dataclass(frozen=True, slots=True)
class MethodInputSchema:
    """Flattened method fields plus the identity of its unpacked public schema."""

    fields: tuple[InputField, ...]
    unpacked: type[object] | None = None


def inspect_method_input(
    signature: inspect.Signature,
    hints: Mapping[str, object],
    *,
    operation_id: str,
    path: str,
    self_parameter: str,
    body_projection: BodyProjection[object, object] | None = None,
) -> MethodInputSchema:
    """Compile a decorated API method signature into the existing slot metadata."""

    fields: list[InputField] = []
    python_names: set[str] = set()
    unpacked_type: type[object] | None = None
    for parameter in signature.parameters.values():
        if parameter.name == self_parameter:
            continue
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise PlanError(f"operation {operation_id!r} cannot declare variadic parameters")
        if parameter.name == "options":
            continue
        declared_annotation = hints.get(parameter.name)
        if declared_annotation is None:
            raise PlanError(
                f"input field {parameter.name!r} in {operation_id!r} requires an annotation"
            )
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            unpacked_type = _append_unpacked_fields(
                fields,
                python_names,
                declared_annotation,
                operation_id=operation_id,
                projection=body_projection,
            )
            continue
        if parameter.name in python_names:
            raise PlanError(
                f"duplicate input field {parameter.name!r} in {operation_id!r}"
            )
        python_names.add(parameter.name)
        annotation, metadata, _ = _unwrap(declared_annotation)
        fields.append(
            _input_field(
                parameter.name,
                annotation,
                metadata,
                required=parameter.default is inspect.Parameter.empty,
                operation_id=operation_id,
            )
        )

    identities: set[tuple[RequestLocation, str]] = set()
    for field in fields:
        if field.location is None or field.wire_name is None:
            continue
        identity = (
            field.location,
            field.wire_name.casefold()
            if field.location is RequestLocation.HEADER
            else field.wire_name,
        )
        if identity in identities:
            raise PlanError(
                f"duplicate {field.location.value} input wire name {field.wire_name!r} "
                f"in {operation_id!r}"
            )
        identities.add(identity)

    _validate_body(
        fields,
        body_fields=[field for field in fields if field.is_body_field],
        root_bodies=[field for field in fields if field.is_root_body],
        projection=body_projection,
        operation_id=operation_id,
    )
    _validate_projection_source(
        body_projection,
        unpacked_type=unpacked_type,
        operation_id=operation_id,
    )
    _validate_path(fields, path=path, operation_id=operation_id)
    return MethodInputSchema(tuple(fields), unpacked_type)


def _append_unpacked_fields(
    fields: list[InputField],
    python_names: set[str],
    declared_annotation: object,
    *,
    operation_id: str,
    projection: BodyProjection[object, object] | None,
) -> type[object]:
    if projection is not None and not is_typeddict(projection.source):
        raise PlanError(
            f"body projection source for {operation_id!r} must be a TypedDict"
        )
    if get_origin(declared_annotation) is not Unpack:
        raise PlanError(
            f"variadic keyword input in {operation_id!r} must be Unpack[TypedDict]"
        )
    unpacked = get_args(declared_annotation)
    if len(unpacked) != 1 or not is_typeddict(unpacked[0]):
        raise PlanError(
            f"variadic keyword input in {operation_id!r} must be Unpack[TypedDict]"
        )
    unpacked_type = unpacked[0]
    projection_names = (
        {field.name for field in TypedDictModelAdapter().fields(projection.source)}
        if projection is not None and is_typeddict(projection.source)
        else set()
    )
    for model_field in TypedDictModelAdapter().fields(unpacked_type):
        if model_field.name == "options":
            raise PlanError(
                f"unpacked input in {operation_id!r} cannot declare reserved field 'options'"
            )
        if model_field.name in python_names:
            raise PlanError(
                f"duplicate input field {model_field.name!r} in {operation_id!r}"
            )
        python_names.add(model_field.name)
        is_projection_source = model_field.name in projection_names
        if is_projection_source and any(
            isinstance(item, _PLACEMENT_TYPES) for item in model_field.metadata
        ):
            raise PlanError(
                f"body projection source field {model_field.name!r} in "
                f"{operation_id!r} also declares a placement"
            )
        fields.append(
            _input_field(
                model_field.name,
                model_field.annotation,
                model_field.metadata,
                required=model_field.required,
                operation_id=operation_id,
                allow_unplaced=is_projection_source,
            )
        )
    return cast(type[object], unpacked_type)


def _input_field(
    python_name: str,
    annotation: object,
    metadata: tuple[object, ...],
    *,
    required: bool,
    operation_id: str,
    allow_unplaced: bool = False,
) -> InputField:
    placements = tuple(item for item in metadata if isinstance(item, _PLACEMENT_TYPES))
    unknown = tuple(item for item in metadata if not isinstance(item, _PLACEMENT_TYPES))
    if unknown:
        names = ", ".join(type(item).__name__ for item in unknown)
        raise PlanError(
            f"input field {python_name!r} in {operation_id!r} has unknown markers: {names}"
        )
    if not placements and allow_unplaced:
        return InputField(
            python_name=python_name,
            wire_name=None,
            annotation=annotation,
            required=required,
            location=None,
            placement=None,
        )
    if len(placements) != 1:
        detail = "no placement" if not placements else "multiple placements"
        raise PlanError(f"input field {python_name!r} in {operation_id!r} has {detail}")
    placement: Placement = placements[0]
    location = _location(placement)
    wire_name = _wire_name(placement, python_name)
    placement = _normalize_placement(placement, wire_name)
    _validate_query_cardinality(
        annotation,
        placement,
        field_name=python_name,
        operation_id=operation_id,
    )
    return InputField(
        python_name=python_name,
        wire_name=wire_name,
        annotation=annotation,
        required=required,
        location=location,
        placement=placement,
    )


def _unwrap(annotation: object) -> tuple[object, tuple[object, ...], None]:
    metadata: list[object] = []
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation, *extras = get_args(annotation)
            metadata.extend(extras)
            continue
        return annotation, tuple(metadata), None


def _location(placement: Placement) -> RequestLocation:
    if isinstance(placement, Path):
        return RequestLocation.PATH
    if isinstance(placement, Query | QueryString):
        return RequestLocation.QUERY
    if isinstance(placement, Header):
        return RequestLocation.HEADER
    if isinstance(placement, Cookie):
        return RequestLocation.COOKIE
    return RequestLocation.BODY


def _wire_name(placement: Placement, python_name: str) -> str:
    name = getattr(placement, "name", None)
    if name is None:
        return python_name
    if not isinstance(name, str) or not name:
        raise PlanError(f"invalid wire name for input field {python_name!r}")
    return name


def _normalize_placement(placement: Placement, wire_name: str) -> Placement:
    if isinstance(placement, Query | Path | Header | Cookie | JsonField | Form | Part):
        return replace(placement, name=wire_name)
    return placement


def _validate_query_cardinality(
    annotation: object,
    placement: object,
    *,
    field_name: str,
    operation_id: str,
) -> None:
    if not isinstance(placement, Query):
        return
    if placement.style != "form" or not placement.explode:
        return
    if placement.codec is not None:
        return
    if _contains_array_annotation(annotation):
        raise PlanError(
            f"query input {field_name!r} in {operation_id!r} would repeat wire name; "
            "use explode=False or an explicit single-value scalar codec"
        )


def _contains_array_annotation(annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin in {types.UnionType, Union}:
        return any(_contains_array_annotation(item) for item in get_args(annotation))
    return origin in {list, tuple, set, frozenset}


def _validate_body(
    fields: list[InputField],
    *,
    body_fields: list[InputField],
    root_bodies: list[InputField],
    projection: BodyProjection[object, object] | None,
    operation_id: str,
) -> None:
    if len(root_bodies) > 1:
        raise PlanError(f"operation {operation_id!r} declares multiple root request bodies")
    if root_bodies and body_fields:
        raise PlanError(f"operation {operation_id!r} mixes a root body with body fields")
    if projection is not None and (root_bodies or body_fields):
        raise PlanError(
            f"operation {operation_id!r} mixes a body projection with flat or root body fields"
        )
    field_kinds = {type(field.placement) for field in body_fields}
    if len(field_kinds) > 1:
        raise PlanError(f"operation {operation_id!r} mixes incompatible body field codecs")
    querystring = [field for field in fields if isinstance(field.placement, QueryString)]
    query = [
        field
        for field in fields
        if field.location is RequestLocation.QUERY and not isinstance(field.placement, QueryString)
    ]
    if querystring and query:
        raise PlanError(f"operation {operation_id!r} mixes QueryString with query fields")


def _validate_path(fields: list[InputField], *, path: str, operation_id: str) -> None:
    declared = {
        field.wire_name
        for field in fields
        if field.location is RequestLocation.PATH and field.wire_name is not None
    }
    expressions = set(_PATH_EXPRESSION.findall(path))
    missing = expressions - declared
    extra = declared - expressions
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)!r}")
        if extra:
            details.append(f"unknown {sorted(extra)!r}")
        raise PlanError(
            f"path inputs for operation {operation_id!r} do not match template: "
            + ", ".join(details)
        )


def _validate_projection_source(
    projection: BodyProjection[object, object] | None,
    *,
    unpacked_type: type[object] | None,
    operation_id: str,
) -> None:
    if projection is None:
        return
    if not is_typeddict(projection.source):
        raise PlanError(
            f"body projection source for {operation_id!r} must be a TypedDict"
        )
    if unpacked_type is None:
        raise PlanError(
            f"body projection for {operation_id!r} requires Unpack[TypedDict] method input"
        )
    adapter = TypedDictModelAdapter()
    public_fields = {field.name: field for field in adapter.fields(unpacked_type)}
    for source_field in adapter.fields(projection.source):
        public_field = public_fields.get(source_field.name)
        if public_field is None:
            raise PlanError(
                f"body projection source field {source_field.name!r} is not present in "
                f"the public input for {operation_id!r}"
            )
        if public_field.annotation != source_field.annotation:
            raise PlanError(
                f"body projection source field {source_field.name!r} in {operation_id!r} "
                "has an incompatible annotation"
            )
        if public_field.required is not source_field.required:
            raise PlanError(
                f"body projection source field {source_field.name!r} in {operation_id!r} "
                "has incompatible requiredness"
            )
