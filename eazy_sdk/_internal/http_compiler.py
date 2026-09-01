"""Lower compact endpoint declarations into the shared execution plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast, get_args, get_origin

from eazy_sdk.codecs import BodyCodec
from eazy_sdk.crypto import PayloadCrypto
from eazy_sdk.dependencies import Inject
from eazy_sdk.models import ModelAdapterError, ModelAdapterRegistry, default_model_adapters
from eazy_sdk.request import BodyProjection, Cookie, Header, Query
from eazy_sdk.request.descriptors import (
    BytesBody,
    FormBody,
    JsonBody,
    JsonField,
    MultipartBody,
    ReplayableStreamBody,
)
from eazy_sdk.request.signatures import (
    SignaturePlan,
    _body_output_path,
    compile_signatures,
)

from .errors import (
    BindingError,
    OperationBindingError,
    PlanError,
    SlotBindingError,
    SlotValueError,
)
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
    private_binding_slots: Mapping[object, ValueSlot[object]]
    private_body_writers: tuple[PrivateBodyWriter, ...]
    signature_plan: SignaturePlan
    body_signature_paths: tuple[tuple[str, ...], ...]
    input_fields: tuple[InputField, ...]
    input_slots: Mapping[str, ValueSlot[object]]
    descriptors: Mapping[ValueSlot[object], object]
    wire_names: Mapping[ValueSlot[object], str]
    pass_diagnostics: tuple[CompilerPassDiagnostic, ...]

    def bind_input(self, request: Mapping[str, object]) -> BoundArguments:
        unknown = request.keys() - self.input_slots.keys()
        if unknown:
            names = sorted(unknown)
            raise OperationBindingError(
                code="unknown_input",
                operation_id=self.contract.operation_id,
                field=names[0] if len(names) == 1 else None,
                phase="bind",
                detail="unknown input value",
            )
        arguments = BoundArguments(
            tuple(Bind(self.input_slots[name], value) for name, value in request.items())
        )
        try:
            OperationValues.from_bound(self.plan.shape, arguments)
        except SlotValueError as exc:
            field = next(
                (name for name, slot in self.input_slots.items() if slot is exc.slot),
                None,
            )
            if field is not None and exc.path is not None:
                field = f"{field}.{exc.path}"
            raise OperationBindingError(
                code="invalid_value",
                operation_id=self.contract.operation_id,
                field=field,
                phase="bind",
                detail="input value is invalid",
                debug_slot=getattr(exc.slot, "diagnostic_name", None),
            ) from None
        except SlotBindingError as exc:
            field = next(
                (name for name, slot in self.input_slots.items() if slot is exc.slot),
                None,
            )
            raise OperationBindingError(
                code=exc.reason,
                operation_id=self.contract.operation_id,
                field=field,
                phase="bind",
                detail="required input value is missing",
                debug_slot=getattr(exc.slot, "diagnostic_name", None),
            ) from None
        except BindingError:
            raise OperationBindingError(
                code="binding_failed",
                operation_id=self.contract.operation_id,
                field=None,
                phase="bind",
                detail="operation input binding failed",
            ) from None
        return arguments


@dataclass(frozen=True, slots=True)
class PrivateWireWriter:
    """One compile-time checked reserved write into a projected target document."""

    path: tuple[str, ...]
    validation_path: tuple[str, ...]
    requirement: object
    result_field: str


@dataclass(frozen=True, slots=True)
class PrivateBodyWriter:
    """One managed protection-state write into a semantic request document."""

    path: tuple[str, ...]
    slot: ValueSlot[object]


@dataclass(frozen=True, slots=True)
class ManagedCookieSetDescriptor:
    """Internal layout marker for a dynamic, validated private cookie set."""


@dataclass(frozen=True, slots=True)
class CompilerPassDiagnostic:
    """Safe structural diagnostic emitted by one private compiler pass."""

    name: str
    item_count: int
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _InputLayoutPass:
    contract: EndpointLike
    operation: OperationIdentity
    slot_groups: dict[RequestLocation, dict[str, ValueSlot[object]]]
    slots: list[ValueSlot[object]]
    descriptors: dict[ValueSlot[object], object]
    wire_names: dict[ValueSlot[object], str]
    slot_locations: dict[ValueSlot[object], RequestLocation | None]
    input_fields: tuple[InputField, ...]
    input_slots: dict[str, ValueSlot[object]]
    projection_slots: dict[str, ValueSlot[object]]
    body_slot: ValueSlot[object] | None
    body_field_slots: dict[str, ValueSlot[object]]
    body_projection: BodyProjection[object, object] | None
    diagnostic: CompilerPassDiagnostic


@dataclass(frozen=True, slots=True)
class _WriterPass:
    layout: _InputLayoutPass
    slot_groups: dict[RequestLocation, dict[str, ValueSlot[object]]]
    slots: list[ValueSlot[object]]
    descriptors: dict[ValueSlot[object], object]
    wire_names: dict[ValueSlot[object], str]
    slot_locations: dict[ValueSlot[object], RequestLocation | None]
    private_binding_slots: Mapping[object, ValueSlot[object]]
    private_body_writers: tuple[PrivateBodyWriter, ...]
    private_wire_writers: tuple[PrivateWireWriter, ...]
    signature_plan: SignaturePlan
    body_signature_paths: tuple[tuple[str, ...], ...]
    models: ModelAdapterRegistry
    diagnostic: CompilerPassDiagnostic


@dataclass(frozen=True, slots=True)
class _GraphPass:
    nodes: tuple[PlanNode, ...]
    diagnostic: CompilerPassDiagnostic


@dataclass(frozen=True, slots=True)
class _FingerprintPass:
    context: tuple[str, ...]
    slots: tuple[tuple[str, str, bool, str], ...]
    diagnostic: CompilerPassDiagnostic


@dataclass(frozen=True, slots=True)
class _ResponseCapabilityPass:
    responses: object
    scope: RequestScope
    requirements: WireRequirements
    replay: CompiledReplayPolicy
    diagnostic: CompilerPassDiagnostic


def compile_endpoint[T](
    contract: EndpointLike,
    *,
    registry: HttpCompilerRegistry | None = None,
    scope: RequestScope | None = None,
    source: SourcePointer | None = None,
    requirements: WireRequirements | None = None,
    fingerprint_context: tuple[str, ...] = (),
    private_bindings: tuple[object, ...] = (),
) -> CompiledContract[T]:
    registry = registry or CompilerRegistry(HTTP_COMPILER_KIND)
    if registry.kind is not HTTP_COMPILER_KIND:
        raise PlanError(f"HTTP compiler received {registry.kind.name!r} registry; expected 'http'")
    scope = scope or RequestScope()
    requirements = requirements or WireRequirements()
    if not contract.operation_id:
        raise PlanError("operation_id must not be empty")
    layout = _compile_input_layout(contract, source=source)
    writers = _compile_writer_pass(layout, private_bindings)
    operation = layout.operation
    slot_groups = writers.slot_groups
    slots = writers.slots
    descriptors = writers.descriptors
    wire_names = writers.wire_names
    input_fields = layout.input_fields
    input_slots = layout.input_slots
    projection_slots = layout.projection_slots
    body_slot = layout.body_slot
    body_field_slots = layout.body_field_slots
    body_projection = layout.body_projection
    private_binding_slots = writers.private_binding_slots
    private_body_writers = writers.private_body_writers
    private_wire_writers = writers.private_wire_writers
    signature_plan = writers.signature_plan
    body_signature_paths = writers.body_signature_paths
    shape = OperationShape(tuple(slots))
    crypto = getattr(contract, "crypto", None)
    graph = _compile_graph_pass(writers, registry, crypto)
    nodes = graph.nodes
    response_capabilities = _compile_response_capability_pass(
        contract,
        scope,
        requirements,
    )
    fingerprints = _compile_fingerprint_pass(
        writers,
        contract,
        crypto,
        fingerprint_context,
    )
    plan: ExecutionPlan[T] = compile_plan(
        operation=operation,
        shape=shape,
        nodes=nodes,
        responses=response_capabilities.responses,
        scope=response_capabilities.scope,
        requirements=response_capabilities.requirements,
        replay=response_capabilities.replay,
        fingerprint_context=fingerprints.context,
        slot_fingerprint=fingerprints.slots,
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
        private_binding_slots=private_binding_slots,
        private_body_writers=private_body_writers,
        signature_plan=signature_plan,
        body_signature_paths=body_signature_paths,
        input_fields=input_fields,
        input_slots=input_slots,
        descriptors=descriptors,
        wire_names=wire_names,
        pass_diagnostics=(
            layout.diagnostic,
            writers.diagnostic,
            graph.diagnostic,
            response_capabilities.diagnostic,
            fingerprints.diagnostic,
        ),
    )


def _compile_input_layout(
    contract: EndpointLike,
    *,
    source: SourcePointer | None,
) -> _InputLayoutPass:
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
    body_projection = cast(
        BodyProjection[object, object] | None,
        getattr(contract, "body_projection", None),
    )
    return _InputLayoutPass(
        contract,
        operation,
        slot_groups,
        slots,
        descriptors,
        wire_names,
        slot_locations,
        input_fields,
        input_slots,
        projection_slots,
        body_slot,
        body_field_slots,
        body_projection,
        CompilerPassDiagnostic(
            "input-layout",
            len(slots),
            tuple(location.value for location in RequestLocation),
        ),
    )


def _compile_writer_pass(
    layout: _InputLayoutPass,
    private_bindings: tuple[object, ...],
) -> _WriterPass:
    slot_groups = {location: dict(group) for location, group in layout.slot_groups.items()}
    slots = list(layout.slots)
    descriptors = dict(layout.descriptors)
    wire_names = dict(layout.wire_names)
    slot_locations = dict(layout.slot_locations)
    private_binding_slots, private_body_writers = _reserve_private_bindings(
        private_bindings,
        slot_groups=slot_groups,
        slots=slots,
        descriptors=descriptors,
        wire_names=wire_names,
        slot_locations=slot_locations,
        body_slot=layout.body_slot,
        body_field_slots=layout.body_field_slots,
        body_projection=layout.body_projection,
    )
    models = default_model_adapters()
    private_wire_writers = (
        _compile_private_wire_writers(layout.body_projection.target, models)
        if layout.body_projection is not None
        else ()
    )
    declared_protections = tuple(getattr(layout.contract, "protections", ()))
    return _finish_writer_pass(
        layout,
        slot_groups,
        slots,
        descriptors,
        wire_names,
        slot_locations,
        private_binding_slots,
        private_body_writers,
        private_wire_writers,
        models,
        declared_protections,
    )


def _finish_writer_pass(
    layout: _InputLayoutPass,
    slot_groups: dict[RequestLocation, dict[str, ValueSlot[object]]],
    slots: list[ValueSlot[object]],
    descriptors: dict[ValueSlot[object], object],
    wire_names: dict[ValueSlot[object], str],
    slot_locations: dict[ValueSlot[object], RequestLocation | None],
    private_binding_slots: Mapping[object, ValueSlot[object]],
    private_body_writers: tuple[PrivateBodyWriter, ...],
    private_wire_writers: tuple[PrivateWireWriter, ...],
    models: ModelAdapterRegistry,
    declared_protections: tuple[object, ...],
) -> _WriterPass:
    if private_wire_writers and not declared_protections:
        raise PlanError("FromProtection target fields require operation protections")
    if declared_protections and layout.body_projection is None:
        raise PlanError("mandatory protections require a body projection")
    if declared_protections:
        mapped = {id(writer.requirement) for writer in private_wire_writers}
        declared = {id(requirement) for requirement in declared_protections}
        if mapped != declared:
            raise PlanError(
                "body projection protection writers do not match operation protections"
            )
    signature_plan = compile_signatures(tuple(getattr(layout.contract, "signing", ())))
    body_signature_paths = (
        _compile_body_signature_paths(layout.body_projection, signature_plan, models)
        if layout.body_projection is not None
        else ()
    )
    _validate_body_writer_paths(private_wire_writers, body_signature_paths)
    _validate_managed_body_writer_paths(
        private_body_writers,
        private_wire_writers,
        body_signature_paths,
        layout.body_field_slots,
    )
    writer_count = len(private_wire_writers) + len(private_body_writers)
    return _WriterPass(
        layout,
        slot_groups,
        slots,
        descriptors,
        wire_names,
        slot_locations,
        private_binding_slots,
        private_body_writers,
        private_wire_writers,
        signature_plan,
        body_signature_paths,
        models,
        CompilerPassDiagnostic(
            "projection-private-writers",
            writer_count,
            (f"signature-paths:{len(body_signature_paths)}",),
        ),
    )


def _compile_graph_pass(
    writers: _WriterPass,
    registry: HttpCompilerRegistry,
    crypto: object,
) -> _GraphPass:
    layout = writers.layout
    bind_node = PlanNode("bind", PlanNodeKind.BIND)
    projection_node = (
        PlanNode(
            "body.projection",
            PlanNodeKind.BODY_PROJECTION,
            reads=tuple(layout.projection_slots.values()),
            after=(bind_node,),
        )
        if layout.body_projection is not None
        else None
    )
    private_wire_node = (
        PlanNode(
            "body.private-wire",
            PlanNodeKind.PRIVATE_WIRE,
            after=(projection_node,),
        )
        if writers.private_wire_writers and projection_node is not None
        else None
    )
    outbound_document = (
        PlanNode(
            "crypto.outbound.document",
            PlanNodeKind.OUTBOUND_DOCUMENT_CRYPTO,
            reads=(layout.body_slot,) if layout.body_slot is not None else (),
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
        if writers.signature_plan.signatures
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
    return _GraphPass(
        nodes,
        CompilerPassDiagnostic(
            "crypto-signing-graph",
            len(nodes),
            tuple(node.name for node in nodes),
        ),
    )


def _compile_fingerprint_pass(
    writers: _WriterPass,
    contract: EndpointLike,
    crypto: object,
    base_context: tuple[str, ...],
) -> _FingerprintPass:
    layout = writers.layout
    projection_fingerprint: tuple[str, ...] = ()
    if layout.body_projection is not None:
        try:
            target_adapter = writers.models.adapter_for_type(layout.body_projection.target)
        except ModelAdapterError as exc:
            raise PlanError(
                f"body projection target for {contract.operation_id!r} is unsupported: {exc}"
            ) from exc
        encoding_name = getattr(
            layout.body_projection.encoding,
            "name",
            type(layout.body_projection.encoding).__qualname__,
        )
        projection_fingerprint = (
            f"body-projection:{layout.body_projection.fingerprint_name}",
            f"body-projection-source:{_type_identity(layout.body_projection.source)}",
            f"body-projection-target:{_type_identity(layout.body_projection.target)}",
            f"body-projection-encoding:{type(layout.body_projection.encoding).__module__}."
            f"{type(layout.body_projection.encoding).__qualname__}:{encoding_name}",
            f"body-projection-target-adapter:{target_adapter.name}",
            *writers.models.fingerprint_components(),
        )
    body_writer_fingerprint = (
        *(
            f"private-wire:{'.'.join(writer.path)}:"
            f"{getattr(writer.requirement, 'name', type(writer.requirement).__qualname__)}:"
            f"{writer.result_field}"
            for writer in writers.private_wire_writers
        ),
        *(
            f"managed-private:{'.'.join(writer.path)}"
            for writer in writers.private_body_writers
        ),
        *(f"body-signature:{'.'.join(path)}" for path in writers.body_signature_paths),
    )
    context = (
        *base_context,
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
    )
    slots = tuple(
        (
            slot.diagnostic_name,
            _location_name(writers.slot_locations[slot]),
            slot.required,
            slot.cardinality.value,
        )
        for slot in writers.slots
    )
    return _FingerprintPass(
        context,
        slots,
        CompilerPassDiagnostic(
            "capabilities-fingerprint",
            len(context) + len(slots),
            (f"context:{len(context)}", f"slots:{len(slots)}"),
        ),
    )


def _compile_response_capability_pass(
    contract: EndpointLike,
    scope: RequestScope,
    requirements: WireRequirements,
) -> _ResponseCapabilityPass:
    replay = CompiledReplayPolicy(
        max_attempts=1,
        idempotent=contract.method.upper() in {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"},
    )
    return _ResponseCapabilityPass(
        contract.responses,
        scope,
        requirements,
        replay,
        CompilerPassDiagnostic(
            "response-capabilities",
            len(requirements.dimensions),
            (
                f"responses:{type(contract.responses).__name__}",
                f"idempotent:{str(replay.idempotent).lower()}",
            ),
        ),
    )


def _reserve_private_bindings(
    collections: tuple[object, ...],
    *,
    slot_groups: dict[RequestLocation, dict[str, ValueSlot[object]]],
    slots: list[ValueSlot[object]],
    descriptors: dict[ValueSlot[object], object],
    wire_names: dict[ValueSlot[object], str],
    slot_locations: dict[ValueSlot[object], RequestLocation | None],
    body_slot: ValueSlot[object] | None,
    body_field_slots: Mapping[str, ValueSlot[object]],
    body_projection: BodyProjection[object, object] | None,
) -> tuple[dict[object, ValueSlot[object]], tuple[PrivateBodyWriter, ...]]:
    from eazy_sdk.protection import (
        PrivateBinding,
        PrivateBindings,
        PrivateCookieSetBinding,
    )

    output: dict[object, ValueSlot[object]] = {}
    body_writers: list[PrivateBodyWriter] = []
    destinations: dict[tuple[RequestLocation, str], object] = {}
    dynamic_cookie_index = 0
    for collection in collections:
        if not isinstance(collection, PrivateBindings):
            raise PlanError("managed protection apply must be PrivateBindings")
        for target in collection.targets:
            if target in output:
                continue
            if isinstance(target, PrivateCookieSetBinding):
                dynamic_cookie_index += 1
                name = f"<managed-cookie-set:{dynamic_cookie_index}>"
                slot = cast(
                    ValueSlot[object],
                    ValueSlot(
                        f"protection.private.cookie-set.{dynamic_cookie_index}",
                        PythonTypeValidator(object),
                        required=False,
                        secret=True,
                    ),
                )
                slot_groups[RequestLocation.COOKIE][name] = slot
                descriptors[slot] = ManagedCookieSetDescriptor()
                wire_names[slot] = name
                slot_locations[slot] = RequestLocation.COOKIE
                slots.append(slot)
                output[target] = slot
                continue
            if not isinstance(target, PrivateBinding):
                raise PlanError(
                    f"unsupported private binding target: {type(target).__name__}"
                )
            location = RequestLocation(target.location.value)
            canonical_name = (
                target.name.casefold()
                if location is RequestLocation.HEADER
                else target.name
            )
            destination = (location, canonical_name)
            if destination in destinations:
                raise PlanError(
                    f"private binding destination is declared more than once: "
                    f"{location.value}.{target.name}"
                )
            destinations[destination] = target
            if location is RequestLocation.BODY:
                path = tuple(target.name.split("."))
                if any(not item for item in path):
                    raise PlanError("private body binding path contains an empty component")
                if body_projection is None and body_slot is None and not body_field_slots:
                    raise PlanError("private body binding requires a semantic request body")
                if body_projection is None:
                    body_descriptors = (
                        (descriptors[body_slot],)
                        if body_slot is not None
                        else tuple(descriptors[slot] for slot in body_field_slots.values())
                    )
                    if not body_descriptors or not all(
                        isinstance(item, JsonBody | JsonField)
                        for item in body_descriptors
                    ):
                        raise PlanError(
                            "private body bindings currently require a JSON body"
                        )
                slot = cast(
                    ValueSlot[object],
                    ValueSlot(
                        f"protection.private.body.{target.name}",
                        PythonTypeValidator(object),
                        required=False,
                        secret=True,
                    ),
                )
                body_writers.append(PrivateBodyWriter(path, slot))
            else:
                group = slot_groups[location]
                collision = next(
                    (
                        name
                        for name in group
                        if (name.casefold() if location is RequestLocation.HEADER else name)
                        == canonical_name
                    ),
                    None,
                )
                if collision is not None:
                    raise PlanError(
                        f"private binding conflicts with an existing writer: "
                        f"{location.value}.{target.name}"
                    )
                slot = cast(
                    ValueSlot[object],
                    ValueSlot(
                        f"protection.private.{location.value}.{target.name}",
                        PythonTypeValidator(object),
                        required=False,
                        secret=True,
                    ),
                )
                group[target.name] = slot
                if location is RequestLocation.HEADER:
                    descriptors[slot] = Header(target.name)
                elif location is RequestLocation.QUERY:
                    descriptors[slot] = Query(target.name)
                else:
                    descriptors[slot] = Cookie(target.name)
                wire_names[slot] = target.name
            slots.append(slot)
            slot_locations[slot] = location
            output[target] = slot
    return output, tuple(body_writers)


def _validate_managed_body_writer_paths(
    managed: tuple[PrivateBodyWriter, ...],
    mandatory: tuple[PrivateWireWriter, ...],
    signature_paths: tuple[tuple[str, ...], ...],
    public_fields: Mapping[str, ValueSlot[object]],
) -> None:
    paths = [writer.path for writer in managed]
    occupied = [writer.path for writer in mandatory]
    occupied.extend(signature_paths)
    occupied.extend((name,) for name in public_fields)
    for index, path in enumerate(paths):
        for other in (*paths[index + 1 :], *occupied):
            common = min(len(path), len(other))
            if path[:common] == other[:common]:
                raise PlanError(
                    "private managed writer path conflicts with another writer: "
                    f"{'.'.join(path)} and {'.'.join(other)}"
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
