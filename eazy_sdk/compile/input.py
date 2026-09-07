"""Compile the fields of an operation class into request field metadata."""

from __future__ import annotations

import re
import types
from dataclasses import dataclass, replace
from typing import (
    Annotated,
    Any,
    Union,
    get_args,
    get_origin,
    is_typeddict,
)

from eazy_sdk.codecs import BodyCodec
from eazy_sdk.core.errors import PlanError
from eazy_sdk.core.http import RequestLocation
from eazy_sdk.models.adapters import (
    ModelAdapterRegistry,
    ModelField,
    UnsupportedModelTypeError,
    unroll_alias,
    unwrap_annotated,
)
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
from eazy_sdk.sentinels import Unset

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
_LIBRARY_METADATA_MODULES = ("pydantic", "msgspec", "annotated_types")
"""Metadata a model library puts next to ours (``Field(...)``, ``Meta(...)``): not a marker."""


@dataclass(frozen=True, slots=True)
class InputField:
    python_name: str
    wire_name: str | None
    annotation: object
    required: bool
    location: RequestLocation | None
    placement: Placement | None
    omittable: bool = False
    """``Omittable[T]``: the value may be ``UNSET``, and ``UNSET`` is never sent."""

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
    """The flattened fields of one operation class, and the class they were read from."""

    fields: tuple[InputField, ...]
    operation_type: type[object] | None = None


def inspect_operation_input(
    operation_type: type[object],
    *,
    operation_id: str,
    path: str,
    models: ModelAdapterRegistry,
    projection: BodyProjection[Any, Any] | None = None,
) -> MethodInputSchema:
    """Read an operation class into the field metadata the compiler consumes.

    The one reader for both authoring forms: ``op(GetOrder)`` hands its class over
    directly, the decorator hands over the class it synthesized. Diagnostics D-02..D-12 of
    the phase-50 plan live here.
    """

    name = operation_type.__name__
    if is_typeddict(operation_type):
        raise PlanError(f"operation class {name} is not a model any configured adapter supports")
    try:
        adapter = models.adapter_for_type(operation_type)
    except UnsupportedModelTypeError as exc:
        raise PlanError(
            f"operation class {name} is not a model any configured adapter supports"
        ) from exc
    _validate_base_order(operation_type)
    if adapter.frozen(operation_type) is False:
        raise PlanError(
            f"operation class {name} must be frozen: use @dataclass(frozen=True) / "
            "msgspec.Struct(frozen=True) / ConfigDict(frozen=True)"
        )

    fields: list[InputField] = []
    for model_field in adapter.fields(operation_type):
        if model_field.name == "options":
            raise PlanError(
                f"operation class {name} declares field 'options', which is reserved "
                "for CallOptions"
            )
        fields.append(
            _input_field(
                model_field,
                operation_id=operation_id,
                allow_unplaced=projection is not None,
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

    _validate_projection_source(
        projection,
        fields=fields,
        operation_type=operation_type,
        models=models,
        operation_id=operation_id,
    )
    _validate_body(
        fields,
        body_fields=[field for field in fields if field.is_body_field],
        root_bodies=[field for field in fields if field.is_root_body],
        projection=projection,
        operation_id=operation_id,
    )
    _validate_path(fields, path=path, operation_id=operation_id)
    return MethodInputSchema(tuple(fields), operation_type)


def _validate_base_order(operation_type: type[object]) -> None:
    """D-03: a Pydantic operation lists ``BaseModel`` before the operation base."""

    from eazy_sdk.operation import HttpOperation

    bases = operation_type.__bases__
    model_index = next(
        (index for index, base in enumerate(bases) if _is_pydantic_model(base)), None
    )
    if model_index is None:
        return
    operation_base = next(
        (
            base
            for base in bases[:model_index]
            if isinstance(base, type) and issubclass(base, HttpOperation)
        ),
        None,
    )
    if operation_base is None:
        return
    name = operation_type.__name__
    base_name = operation_base.__name__
    raise PlanError(
        f"operation class {name} must list BaseModel before {base_name}: "
        f"class {name}(BaseModel, {base_name}[...])"
    )


def _is_pydantic_model(cls: type[object]) -> bool:
    return any(
        base.__module__.startswith("pydantic") and base.__name__ == "BaseModel"
        for base in cls.__mro__
    )


def _input_field(
    model_field: ModelField,
    *,
    operation_id: str,
    allow_unplaced: bool,
) -> InputField:
    python_name = model_field.name
    annotation, extra = flatten_annotation(model_field.annotation)
    metadata = _dedupe((*model_field.metadata, *extra))
    annotation, omittable = _strip_unset(annotation)
    if omittable and model_field.required:
        raise PlanError(
            f"input field {python_name!r} in {operation_id!r} is Omittable but has no "
            "default; give it UNSET"
        )
    placements = tuple(item for item in metadata if isinstance(item, _PLACEMENT_TYPES))
    unknown = tuple(
        item
        for item in metadata
        if not isinstance(item, _PLACEMENT_TYPES) and not _is_library_metadata(item)
    )
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
            required=model_field.required,
            location=None,
            placement=None,
            omittable=omittable,
        )
    if len(placements) != 1:
        detail = "no placement" if not placements else "multiple placements"
        raise PlanError(f"input field {python_name!r} in {operation_id!r} has {detail}")
    placement: Placement = placements[0]
    location = _location(placement)
    wire_name = _wire_name(placement, model_field, operation_id=operation_id)
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
        required=model_field.required,
        location=location,
        placement=placement,
        omittable=omittable,
    )


def _dedupe(metadata: tuple[object, ...]) -> tuple[object, ...]:
    seen: set[int] = set()
    output: list[object] = []
    for item in metadata:
        if id(item) in seen:
            continue
        seen.add(id(item))
        output.append(item)
    return tuple(output)


def _is_library_metadata(item: object) -> bool:
    module = type(item).__module__ or ""
    return module.split(".", 1)[0] in _LIBRARY_METADATA_MODULES


def flatten_annotation(annotation: object) -> tuple[object, tuple[object, ...]]:
    """Collect ``Annotated`` metadata wherever it sits: outermost, inside a union, in an alias.

    ``Query[int] | None`` is ``Annotated[int, Query()] | None``; the marker is one level
    down, and the field type is ``int | None``. ``Omittable[Query[int]]`` is the same with an
    alias on top. The compiler sees one annotation whichever way the author nested them.
    """

    annotation = unroll_alias(annotation)
    metadata: list[object] = []
    while get_origin(annotation) is Annotated:
        inner, *extras = get_args(annotation)
        metadata.extend(extras)
        annotation = unroll_alias(inner)
    if get_origin(annotation) in {types.UnionType, Union}:
        members: list[object] = []
        for member in get_args(annotation):
            inner, nested = flatten_annotation(member)
            metadata.extend(nested)
            if inner not in members:
                members.append(inner)
        annotation = members[0] if len(members) == 1 else Union[tuple(members)]  # noqa: UP007
    return annotation, tuple(metadata)


def _strip_unset(annotation: object) -> tuple[object, bool]:
    """``int | Unset`` → ``(int, True)``; anything without ``Unset`` is returned as is."""

    unrolled = unroll_alias(annotation)
    if get_origin(unrolled) not in {types.UnionType, Union}:
        return annotation, False
    members: tuple[object, ...] = get_args(unrolled)
    if Unset not in members:
        return annotation, False
    remaining = tuple(member for member in members if member is not Unset)
    if not remaining:
        raise PlanError("a field cannot be only Unset")
    if len(remaining) == 1:
        return remaining[0], True
    return Union[remaining], True  # noqa: UP007 - built dynamically


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


def _wire_name(placement: Placement, model_field: ModelField, *, operation_id: str) -> str:
    """I3: the marker says where, the model says what it is called.

    ``m`` is the marker's name, ``w`` the wire name the model library reports, ``v`` the
    validation name (Pydantic's alias when ``serialize_by_alias`` is off), ``p`` the Python
    name. Both sources naming the field is a declaration error; an alias that never reaches
    the wire is one too.
    """

    python_name = model_field.name
    marker_name = getattr(placement, "name", None)
    if marker_name is not None and (not isinstance(marker_name, str) or not marker_name):
        raise PlanError(f"invalid wire name for input field {python_name!r}")
    wire = model_field.wire_name
    validation = model_field.validation_name
    if wire == python_name and validation is not None and validation != python_name:
        raise PlanError(
            f"input field {python_name!r} in {operation_id!r} has alias {validation!r} that "
            "will not reach the wire; set model_config = ConfigDict(serialize_by_alias=True)"
        )
    if marker_name is None:
        return wire if wire != python_name else python_name
    if wire != python_name:
        raise PlanError(
            f"input field {python_name!r} in {operation_id!r} names its wire field twice: "
            f"model says {wire!r}, marker says {marker_name!r}; keep one"
        )
    return marker_name


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
    if origin is Annotated:
        return _contains_array_annotation(get_args(annotation)[0])
    if origin in {types.UnionType, Union}:
        return any(_contains_array_annotation(item) for item in get_args(annotation))
    return origin in {list, tuple, set, frozenset}


def _validate_body(
    fields: list[InputField],
    *,
    body_fields: list[InputField],
    root_bodies: list[InputField],
    projection: BodyProjection[Any, Any] | None,
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
    projection: BodyProjection[Any, Any] | None,
    *,
    fields: list[InputField],
    operation_type: type[object],
    models: ModelAdapterRegistry,
    operation_id: str,
) -> None:
    """The projection reads its source fields off the operation value.

    With no ``source`` the source is the operation class itself and every unplaced field
    feeds the projection. With an explicit ``source`` model, each of its fields must be an
    unplaced field of the operation with the same annotation and no weaker requiredness.
    """

    if projection is None:
        return
    source = projection.source
    if source is None or source is operation_type:
        return
    try:
        source_fields = models.fields(source)
    except UnsupportedModelTypeError as exc:
        raise PlanError(
            f"body projection source for {operation_id!r} is not a model any configured "
            "adapter supports"
        ) from exc
    public_fields = {field.python_name: field for field in fields if field.is_projection_source}
    source_names = {source_field.name for source_field in source_fields}
    for field in fields:
        if field.is_projection_source and field.python_name not in source_names:
            raise PlanError(
                f"input field {field.python_name!r} in {operation_id!r} has no placement"
            )
        if not field.is_projection_source and field.python_name in source_names:
            raise PlanError(
                f"body projection source field {field.python_name!r} in "
                f"{operation_id!r} also declares a placement"
            )
    for source_field in source_fields:
        public_field = public_fields.get(source_field.name)
        if public_field is None:
            raise PlanError(
                f"body projection source field {source_field.name!r} is not present in "
                f"the public input for {operation_id!r}"
            )
        source_annotation, _ = _strip_unset(unwrap_annotated(source_field.annotation)[0])
        if public_field.annotation != source_annotation:
            raise PlanError(
                f"body projection source field {source_field.name!r} in {operation_id!r} "
                "has an incompatible annotation"
            )
        # A public field with a default is always present; only an ``Omittable`` field
        # can be left out, and that is what a required source field cannot tolerate.
        if source_field.required and public_field.omittable:
            raise PlanError(
                f"body projection source field {source_field.name!r} in {operation_id!r} "
                "has incompatible requiredness: it is required but can be omitted "
                "from the public input"
            )
