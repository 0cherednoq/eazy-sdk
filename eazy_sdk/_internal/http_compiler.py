"""Lower compact endpoint declarations into the shared execution plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast, get_args, get_origin

from eazy_sdk.codecs import BodyCodec
from eazy_sdk.crypto import PayloadCrypto
from eazy_sdk.dependencies import Inject
from eazy_sdk.models import ModelAdapterError, ModelAdapterRegistry, default_model_adapters
from eazy_sdk.request import BodyProjection
from eazy_sdk.request.descriptors import (
    BytesBody,
    FormBody,
    JsonBody,
    MultipartBody,
    ReplayableStreamBody,
)
from eazy_sdk.request.signatures import (
    SignaturePlan,
    _body_output_path,
    compile_signatures,
)

from .errors import BindingError, PlanError
from .http import RequestLocation
from .http_plan import (
    CompiledReplayPolicy,
    ExecutionPlan,
    PlanNode,
    PlanNodeKind,
    RequestScope,
    WireRequirements,
    compile_plan,
)
from .input import InputField, _validate_query_cardinality
from .kernel import (
    Bind,
    BoundArguments,
    CompilerKind,
    CompilerRegistry,
    OperationIdentity,
    OperationShape,
    OperationValues,
    PythonTypeValidator,
    SourcePointer,
    ValueSlot,
)


class EndpointLike(Protocol):
    @property
    def operation_id(self) -> str: ...

    @property
    def method(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def input_fields(self) -> tuple[InputField, ...]: ...

    @property
    def responses(self) -> object: ...


HTTP_COMPILER_KIND = CompilerKind[EndpointLike]("http")
type HttpCompilerRegistry = CompilerRegistry[EndpointLike, PlanNode]


@dataclass(frozen=True, slots=True)
class CompiledContract[T]:
    contract: EndpointLike
    plan: ExecutionPlan[T]
    path_slots: Mapping[str, ValueSlot[object]]
    query_slots: Mapping[str, ValueSlot[object]]
    header_slots: Mapping[str, ValueSlot[object]]
    cookie_slots: Mapping[str, ValueSlot[object]]
    body_slot: ValueSlot[object] | None
    body_slots: Mapping[str, ValueSlot[object]]
    body_field_slots: Mapping[str, ValueSlot[object]]
    body_projection: BodyProjection[object, object] | None
    projection_slots: Mapping[str, ValueSlot[object]]
    private_wire_writers: tuple[PrivateWireWriter, ...]
    signature_plan: SignaturePlan
    body_signature_paths: tuple[tuple[str, ...], ...]
    input_fields: tuple[InputField, ...]
    input_slots: Mapping[str, ValueSlot[object]]
    descriptors: Mapping[ValueSlot[object], object]
    wire_names: Mapping[ValueSlot[object], str]

    def bind_input(self, request: Mapping[str, object]) -> BoundArguments:
        unknown = request.keys() - self.input_slots.keys()
        if unknown:
            raise BindingError(
                f"unknown input values for {self.contract.operation_id!r}: {sorted(unknown)}"
            )
        arguments = BoundArguments(
            tuple(Bind(self.input_slots[name], value) for name, value in request.items())
        )
        OperationValues.from_bound(self.plan.shape, arguments)
        return arguments


@dataclass(frozen=True, slots=True)
class PrivateWireWriter:
    """One compile-time checked reserved write into a projected target document."""

    path: tuple[str, ...]
    validation_path: tuple[str, ...]
    requirement: object
    result_field: str


def compile_endpoint[T](
    contract: EndpointLike,
    *,
    registry: HttpCompilerRegistry | None = None,
    scope: RequestScope | None = None,
    source: SourcePointer | None = None,
    requirements: WireRequirements | None = None,
    fingerprint_context: tuple[str, ...] = (),
) -> CompiledContract[T]:
    registry = registry or CompilerRegistry(HTTP_COMPILER_KIND)
    if registry.kind is not HTTP_COMPILER_KIND:
        raise PlanError(f"HTTP compiler received {registry.kind.name!r} registry; expected 'http'")
    scope = scope or RequestScope()
    requirements = requirements or WireRequirements()
    if not contract.operation_id:
        raise PlanError("operation_id must not be empty")
    operation = OperationIdentity(contract.operation_id, source)
    slot_groups: dict[RequestLocation, dict[str, ValueSlot[object]]] = {
        location: {} for location in RequestLocation
    }
    slots: list[ValueSlot[object]] = []
    descriptors: dict[ValueSlot[object], object] = {}
    wire_names: dict[ValueSlot[object], str] = {}
    slot_locations: dict[ValueSlot[object], RequestLocation | None] = {}
    input_fields = contract.input_fields
    input_slots: dict[str, ValueSlot[object]] = {}
    projection_slots: dict[str, ValueSlot[object]] = {}
    input_identities: set[tuple[RequestLocation, str]] = set()
    for field in input_fields:
        if field.is_projection_source:
            slot = cast(
                ValueSlot[object],
                ValueSlot(
                    diagnostic_name=f"body-projection.source.{field.python_name}",
                    validator=PythonTypeValidator(field.annotation),
                    required=field.required,
                ),
            )
            input_slots[field.python_name] = slot
            projection_slots[field.python_name] = slot
            slots.append(slot)
            slot_locations[slot] = None
            continue
        if field.location is None or field.wire_name is None or field.placement is None:
            raise PlanError(
                f"input field {field.python_name!r} in {contract.operation_id!r} "
                "has incomplete placement metadata"
            )
        identity = (
            field.location,
            field.wire_name.casefold()
            if field.location is RequestLocation.HEADER
            else field.wire_name,
        )
        if identity in input_identities:
            raise PlanError(
                f"duplicate {field.location.value} input wire name {field.wire_name!r} "
                f"in {contract.operation_id!r}"
            )
        input_identities.add(identity)
        _validate_query_cardinality(
            field.annotation,
            field.placement,
            field_name=field.python_name,
            operation_id=contract.operation_id,
        )
        slot = cast(
            ValueSlot[object],
            ValueSlot(
                diagnostic_name=f"{field.location.value}.{field.python_name}",
                validator=PythonTypeValidator(field.annotation),
                required=field.required,
            ),
        )
        input_slots[field.python_name] = slot
        slot_groups[field.location][field.wire_name] = slot
        slots.append(slot)
        descriptors[slot] = field.placement
        wire_names[slot] = field.wire_name
        slot_locations[slot] = field.location
    for injection in getattr(contract, "inject", ()):
        if not isinstance(injection, Inject):
            raise PlanError(f"unsupported Inject declaration: {type(injection).__name__}")
        location = RequestLocation(injection.location)
        wire_name = injection.wire_name
        identity = (
            location,
            wire_name.casefold() if location is RequestLocation.HEADER else wire_name,
        )
        if identity in input_identities:
            raise PlanError(
                f"injected {location.value} wire name collides with another slot: {wire_name!r}"
            )
        input_identities.add(identity)
        injected_slot = cast(
            ValueSlot[object],
            ValueSlot(
                diagnostic_name=f"inject.{location.value}.{wire_name}",
                validator=PythonTypeValidator(object),
                required=False,
                secret=injection.secret,
            ),
        )
        slot_groups[location][wire_name] = injected_slot
        slots.append(injected_slot)
        descriptors[injected_slot] = injection.placement
        wire_names[injected_slot] = wire_name
        slot_locations[injected_slot] = location
    security = getattr(contract, "security", None)
    for scheme in _security_schemes(security):
        for placement in getattr(scheme, "placements", ()):
            location_name = getattr(getattr(placement, "location", None), "value", None)
            try:
                location = RequestLocation(location_name)
            except ValueError as exc:
                raise PlanError(f"unsupported auth placement: {location_name!r}") from exc
            name = getattr(placement, "name", None)
            if not isinstance(name, str) or not name:
                raise PlanError("auth placement requires a wire name")
            group = slot_groups[location]
            if name in group:
                continue
            auth_slot = cast(
                ValueSlot[object],
                ValueSlot(
                    diagnostic_name=f"auth.{getattr(scheme, 'diagnostic_name', 'scheme')}.{name}",
                    validator=PythonTypeValidator(str),
                    required=False,
                    secret=True,
                ),
            )
            group[name] = auth_slot
            slots.append(auth_slot)
            descriptors[auth_slot] = placement
            wire_names[auth_slot] = name
            slot_locations[auth_slot] = location
    body_slot: ValueSlot[object] | None = next(
        (
            input_slots[field.python_name]
            for field in input_fields
            if isinstance(
                field.placement,
                JsonBody | FormBody | MultipartBody | BytesBody | ReplayableStreamBody | BodyCodec,
            )
        ),
        None,
    )
    body_field_slots = {
        field.wire_name: input_slots[field.python_name]
        for field in input_fields
        if field.is_body_field and field.wire_name is not None
    }
    body_field_slots.update(
        {
            injection.wire_name: slot_groups[RequestLocation.BODY][injection.wire_name]
            for injection in getattr(contract, "inject", ())
            if isinstance(injection, Inject) and injection.location == "body"
        }
    )
    shape = OperationShape(tuple(slots))
    bind_node = PlanNode("bind", PlanNodeKind.BIND)
    body_projection = cast(
        BodyProjection[object, object] | None,
        getattr(contract, "body_projection", None),
    )
    projection_node = (
        PlanNode(
            "body.projection",
            PlanNodeKind.BODY_PROJECTION,
            reads=tuple(projection_slots.values()),
            after=(bind_node,),
        )
        if body_projection is not None
        else None
    )
    models = default_model_adapters()
    private_wire_writers = (
        _compile_private_wire_writers(body_projection.target, models)
        if body_projection is not None
        else ()
    )
    declared_protections = tuple(getattr(contract, "protections", ()))
    if private_wire_writers and not declared_protections:
        raise PlanError("FromProtection target fields require operation protections")
    if declared_protections and body_projection is None:
        raise PlanError("mandatory protections require a body projection")
    if declared_protections:
        mapped = {id(writer.requirement) for writer in private_wire_writers}
        declared = {id(requirement) for requirement in declared_protections}
        if mapped != declared:
            raise PlanError(
                "body projection protection writers do not match operation protections"
            )
    signature_plan = compile_signatures(tuple(getattr(contract, "signing", ())))
    body_signature_paths = (
        _compile_body_signature_paths(body_projection, signature_plan, models)
        if body_projection is not None
        else ()
    )
    _validate_body_writer_paths(private_wire_writers, body_signature_paths)
    private_wire_node = (
        PlanNode(
            "body.private-wire",
            PlanNodeKind.PRIVATE_WIRE,
            after=(projection_node,),
        )
        if private_wire_writers and projection_node is not None
        else None
    )
    crypto = getattr(contract, "crypto", None)
    outbound_document = (
        PlanNode(
            "crypto.outbound.document",
            PlanNodeKind.OUTBOUND_DOCUMENT_CRYPTO,
            reads=(body_slot,) if body_slot is not None else (),
            after=(private_wire_node or projection_node or bind_node,),
        )
        if isinstance(crypto, PayloadCrypto)
        and crypto.outbound is not None
        and crypto.outbound.fields
        else None
    )
    prepare_node = PlanNode(
        "prepare",
        PlanNodeKind.PREPARE,
        after=(outbound_document or private_wire_node or projection_node or bind_node,),
    )
    outbound_encoded = (
        PlanNode(
            "crypto.outbound.encoded",
            PlanNodeKind.OUTBOUND_ENCODED_CRYPTO,
            after=(prepare_node,),
        )
        if isinstance(crypto, PayloadCrypto)
        and crypto.outbound is not None
        and crypto.outbound.encoded is not None
        else None
    )
    sign_node = (
        PlanNode(
            "sign",
            PlanNodeKind.SIGN,
            after=(outbound_encoded or prepare_node,),
        )
        if signature_plan.signatures
        else None
    )
    inbound_encoded = (
        PlanNode(
            "crypto.inbound.encoded",
            PlanNodeKind.INBOUND_ENCODED_CRYPTO,
            after=(outbound_encoded or prepare_node,),
        )
        if isinstance(crypto, PayloadCrypto)
        and crypto.inbound is not None
        and crypto.inbound.encoded is not None
        else None
    )
    inbound_document = (
        PlanNode(
            "crypto.inbound.document",
            PlanNodeKind.INBOUND_DOCUMENT_CRYPTO,
            after=(inbound_encoded or outbound_encoded or prepare_node,),
        )
        if isinstance(crypto, PayloadCrypto)
        and crypto.inbound is not None
        and crypto.inbound.fields
        else None
    )
    crypto_nodes = tuple(
        node
        for node in (
            outbound_document,
            outbound_encoded,
            inbound_encoded,
            inbound_document,
        )
        if node is not None
    )
    nodes = tuple(
        node
        for node in (
            bind_node,
            *registry.nodes,
            projection_node,
            private_wire_node,
            prepare_node,
            sign_node,
            *crypto_nodes,
        )
        if node is not None
    )
    projection_fingerprint: tuple[str, ...] = ()
    if body_projection is not None:
        try:
            target_adapter = models.adapter_for_type(body_projection.target)
        except ModelAdapterError as exc:
            raise PlanError(
                f"body projection target for {contract.operation_id!r} is unsupported: {exc}"
            ) from exc
        encoding_name = getattr(
            body_projection.encoding,
            "name",
            type(body_projection.encoding).__qualname__,
        )
        projection_fingerprint = (
            f"body-projection:{body_projection.fingerprint_name}",
            f"body-projection-source:{_type_identity(body_projection.source)}",
            f"body-projection-target:{_type_identity(body_projection.target)}",
            f"body-projection-encoding:{type(body_projection.encoding).__module__}."
            f"{type(body_projection.encoding).__qualname__}:{encoding_name}",
            f"body-projection-target-adapter:{target_adapter.name}",
            *models.fingerprint_components(),
        )
    body_writer_fingerprint = (
        *(
            f"private-wire:{'.'.join(writer.path)}:"
            f"{getattr(writer.requirement, 'name', type(writer.requirement).__qualname__)}:"
            f"{writer.result_field}"
            for writer in private_wire_writers
        ),
        *(f"body-signature:{'.'.join(path)}" for path in body_signature_paths),
    )
    plan: ExecutionPlan[T] = compile_plan(
        operation=operation,
        shape=shape,
        nodes=nodes,
        responses=contract.responses,
        scope=scope,
        requirements=requirements,
        replay=CompiledReplayPolicy(
            max_attempts=1,
            idempotent=contract.method.upper() in {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"},
        ),
        fingerprint_context=(
            *fingerprint_context,
            *projection_fingerprint,
            *body_writer_fingerprint,
            *(
                (
                    f"crypto:{crypto.name}",
                    f"crypto-wire:{type(getattr(contract, 'crypto_wire', None)).__name__}",
                )
                if isinstance(crypto, PayloadCrypto)
                else ()
            ),
        ),
        slot_fingerprint=tuple(
            (
                slot.diagnostic_name,
                _location_name(slot_locations[slot]),
                slot.required,
                slot.cardinality.value,
            )
            for slot in slots
        ),
    )
    return CompiledContract(
        contract=contract,
        plan=plan,
        path_slots=slot_groups[RequestLocation.PATH],
        query_slots=slot_groups[RequestLocation.QUERY],
        header_slots=slot_groups[RequestLocation.HEADER],
        cookie_slots=slot_groups[RequestLocation.COOKIE],
        body_slot=body_slot,
        body_slots=slot_groups[RequestLocation.BODY],
        body_field_slots=body_field_slots,
        body_projection=body_projection,
        projection_slots=projection_slots,
        private_wire_writers=private_wire_writers,
        signature_plan=signature_plan,
        body_signature_paths=body_signature_paths,
        input_fields=input_fields,
        input_slots=input_slots,
        descriptors=descriptors,
        wire_names=wire_names,
    )


def _compile_private_wire_writers(
    target: object,
    models: ModelAdapterRegistry,
) -> tuple[PrivateWireWriter, ...]:
    from eazy_sdk.protection import FromProtection

    writers: list[PrivateWireWriter] = []

    def visit(
        annotation: object,
        path: tuple[str, ...],
        validation_path: tuple[str, ...],
        active: frozenset[object],
    ) -> None:
        nested = _writer_model_type(annotation)
        if nested in active:
            return
        try:
            fields = models.fields(nested)
        except ModelAdapterError:
            return
        next_active = active | {nested}
        for field in fields:
            field_path = (*path, field.wire_name)
            field_validation_path = (
                *validation_path,
                field.validation_name or field.name,
            )
            sources = tuple(
                item for item in field.metadata if isinstance(item, FromProtection)
            )
            if len(sources) > 1:
                raise PlanError(
                    f"{'.'.join(field_path)}: multiple FromProtection writers"
                )
            if sources:
                source = sources[0]
                writers.append(
                    PrivateWireWriter(
                        field_path,
                        field_validation_path,
                        source.requirement,
                        source.field,
                    )
                )
            visit(field.annotation, field_path, field_validation_path, next_active)

    visit(target, (), (), frozenset())
    for index, writer in enumerate(writers):
        for other in writers[index + 1 :]:
            common = min(len(writer.path), len(other.path))
            if writer.path[:common] == other.path[:common]:
                raise PlanError(
                    "private wire writer paths overlap: "
                    f"{'.'.join(writer.path)} and {'.'.join(other.path)}"
                )
    return tuple(writers)


def _writer_model_type(annotation: object) -> object:
    from types import UnionType
    from typing import Union

    origin = get_origin(annotation)
    if origin in {UnionType, Union}:
        candidates = tuple(item for item in get_args(annotation) if item is not type(None))
        if len(candidates) == 1:
            return _writer_model_type(candidates[0])
    return annotation


def _compile_body_signature_paths(
    projection: BodyProjection[object, object],
    signature_plan: SignaturePlan,
    models: ModelAdapterRegistry,
) -> tuple[tuple[str, ...], ...]:
    outputs = tuple(
        output
        for signature in signature_plan.signatures
        for output in signature.outputs
        if output.location is RequestLocation.BODY
    )
    if not outputs:
        return ()
    if not isinstance(projection.encoding, JsonBody | FormBody):
        raise PlanError(
            "projected body signature outputs require JsonBody or FormBody encoding"
        )
    paths = tuple(_body_output_path(output) for output in outputs)
    if isinstance(projection.encoding, FormBody) and any(len(path) != 1 for path in paths):
        raise PlanError("form body signature outputs must select one top-level target field")
    for path in paths:
        _validate_wire_target_path(projection.target, path, models)
    _validate_overlapping_paths(paths, "body signature output")
    return paths


def _validate_wire_target_path(
    target: object,
    path: tuple[str, ...],
    models: ModelAdapterRegistry,
) -> None:
    current = target
    for component in path:
        try:
            fields = models.fields(_writer_model_type(current))
        except ModelAdapterError:
            raise PlanError(
                f"body signature output cannot traverse target path {'.'.join(path)!r}"
            ) from None
        selected = next((field for field in fields if field.wire_name == component), None)
        if selected is None:
            raise PlanError(
                f"body signature output target field is not declared: {'.'.join(path)!r}"
            )
        current = selected.annotation


def _validate_body_writer_paths(
    private_writers: tuple[PrivateWireWriter, ...],
    signature_paths: tuple[tuple[str, ...], ...],
) -> None:
    for writer in private_writers:
        for signature_path in signature_paths:
            common = min(len(writer.path), len(signature_path))
            if writer.path[:common] == signature_path[:common]:
                raise PlanError(
                    "private wire and body signature writers overlap: "
                    f"{'.'.join(writer.path)} and {'.'.join(signature_path)}"
                )


def _validate_overlapping_paths(
    paths: tuple[tuple[str, ...], ...],
    owner: str,
) -> None:
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            common = min(len(path), len(other))
            if path[:common] == other[:common]:
                raise PlanError(
                    f"{owner} paths overlap: {'.'.join(path)} and {'.'.join(other)}"
                )


def _type_identity(annotation: object) -> str:
    module = getattr(annotation, "__module__", type(annotation).__module__)
    name = getattr(annotation, "__qualname__", repr(annotation))
    return f"{module}.{name}"


def _location_name(location: RequestLocation | None) -> str:
    return location.value if location is not None else "projection"


def _security_schemes(security: object | None) -> tuple[object, ...]:
    if security is None:
        return ()
    alternatives = getattr(security, "alternatives", None)
    if alternatives is not None:
        return tuple(
            scheme for alternative in alternatives for scheme in getattr(alternative, "schemes", ())
        )
    schemes = getattr(security, "schemes", None)
    if schemes is not None:
        return tuple(schemes)
    if hasattr(security, "placements"):
        return (security,)
    raise PlanError(f"unsupported security declaration: {type(security).__name__}")
