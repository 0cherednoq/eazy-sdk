"""Stable intermediate representation for the supported OpenAPI subset."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast


class UnsupportedOpenAPIError(ValueError):
    """An OpenAPI construct cannot be represented by the advertised subset."""

    def __init__(self, pointer: str, reason: str, *, operation_id: str | None = None) -> None:
        prefix = f"operation {operation_id!r}: " if operation_id is not None else ""
        super().__init__(f"{prefix}{pointer}: {reason}")
        self.pointer = pointer
        self.reason = reason
        self.operation_id = operation_id


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    document: str
    pointer: str


@dataclass(frozen=True, slots=True)
class Ref[T]:
    identity: SourceIdentity
    target: T


@dataclass(frozen=True, slots=True)
class ParameterIR:
    name: str
    location: str
    type_expression: str
    required: bool
    style: str
    explode: bool
    allow_reserved: bool
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class BodyFieldIR:
    name: str
    type_expression: str
    required: bool


@dataclass(frozen=True, slots=True)
class RequestBodyIR:
    media_type: str
    type_expression: str
    required: bool
    fields: tuple[BodyFieldIR, ...] | None = None
    wire_fields: tuple[BodyFieldIR, ...] | None = None


@dataclass(frozen=True, slots=True)
class BodyProjectionIR:
    source_type_expression: str
    target_type_expression: str
    source_fields: tuple[BodyFieldIR, ...]
    target_fields: tuple[BodyFieldIR, ...]
    application: str
    encoding: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseIR:
    status: int | str
    media_type: str | None
    type_expression: str | None
    success: bool
    item_type_expression: str | None = None


@dataclass(frozen=True, slots=True)
class SecurityRequirementIR:
    schemes: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class SecuritySchemeIR:
    name: str
    kind: str
    location: str | None = None
    wire_name: str | None = None
    scheme: str | None = None


@dataclass(frozen=True, slots=True)
class WireOptionsIR:
    query_order: tuple[str, ...] | None = None
    header_order: tuple[str, ...] | None = None
    cookie_order: tuple[str, ...] | None = None
    body_order: tuple[str, ...] | None = None
    exact: bool = False
    protocol: str | None = None


@dataclass(frozen=True, slots=True)
class DependencyBindingIR:
    field: str | None
    location: str
    name: str
    required: bool


@dataclass(frozen=True, slots=True)
class DependencyIR:
    name: str
    result_type: str
    cache: str
    secret: bool
    bindings: tuple[DependencyBindingIR, ...]


@dataclass(frozen=True, slots=True)
class DependencyRequirementIR:
    dependency: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ProtectionFlowIR:
    name: str
    result_type: str
    acquire: str
    solve: bool = False
    verify: str | None = None


@dataclass(frozen=True, slots=True)
class ProtectionOutputIR:
    target: str
    source: str


@dataclass(frozen=True, slots=True)
class ProtectionUseIR:
    flow: str
    outputs: tuple[ProtectionOutputIR, ...]


@dataclass(frozen=True, slots=True)
class ScopeIR:
    hosts: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    schemes: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    path_patterns: tuple[str, ...] = ()
    endpoint_names: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DomainRuleIR:
    scope: ScopeIR
    requires: tuple[DependencyRequirementIR, ...] = ()
    wire_profile: str | None = None


@dataclass(frozen=True, slots=True)
class SignatureIR:
    name: str
    implementation: str


@dataclass(frozen=True, slots=True)
class CryptoWireIR:
    content_type: str = "application/octet-stream"
    clear_content_type: str = "application/json"
    plaintext_statuses: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class CryptoUseIR:
    profile: str
    direction: str = "bidirectional"
    wire: CryptoWireIR = CryptoWireIR()


@dataclass(frozen=True, slots=True)
class SessionValueIR:
    target: str
    source: str | None = None
    literal: object | None = None
    is_literal: bool = False


@dataclass(frozen=True, slots=True)
class SessionCallIR:
    operation: str
    request_field: str
    request_model: str
    values: tuple[SessionValueIR, ...]


@dataclass(frozen=True, slots=True)
class SessionAuthIR:
    scheme: str
    session_model: str
    credentials_model: str
    bearer_field: str
    refresh_token_field: str | None
    expires_at_field: str | None
    expires_leeway_seconds: float
    acquire: SessionCallIR
    refresh: SessionCallIR | None = None


@dataclass(frozen=True, slots=True)
class ServerVariableIR:
    name: str
    default: str
    enum: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServerIR:
    url: str
    variables: tuple[ServerVariableIR, ...]


@dataclass(frozen=True, slots=True)
class OperationIR:
    operation_id: str
    method: str
    path: str
    parameters: tuple[ParameterIR, ...]
    request_body: RequestBodyIR | None
    responses: tuple[ResponseIR, ...]
    security: tuple[SecurityRequirementIR, ...] | None
    tags: tuple[str, ...]
    server: ServerIR | None
    requires: tuple[DependencyRequirementIR, ...] = ()
    signatures: tuple[str, ...] = ()
    wire: WireOptionsIR | None = None
    idempotent: bool | None = None
    protections: tuple[ProtectionUseIR, ...] = ()
    body_projection: BodyProjectionIR | None = None
    crypto: CryptoUseIR | None = None


@dataclass(frozen=True, slots=True)
class OpenAPIIR:
    version: str
    operations: tuple[OperationIR, ...]
    model_names: frozenset[str]
    dependencies: tuple[DependencyIR, ...] = ()
    domain_rules: tuple[DomainRuleIR, ...] = ()
    references: tuple[Ref[Any], ...] = ()
    security_schemes: tuple[SecuritySchemeIR, ...] = ()
    signature_profiles: tuple[SignatureIR, ...] = ()
    session_auth: SessionAuthIR | None = None
    protection_flows: tuple[ProtectionFlowIR, ...] = ()


_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_LOCATION_DEFAULTS = {
    "path": ("simple", False),
    "query": ("form", True),
    "querystring": ("", False),
    "header": ("simple", False),
    "cookie": ("form", True),
}
_STYLES = {
    "path": {"simple", "label", "matrix"},
    "query": {"form", "spaceDelimited", "pipeDelimited", "deepObject"},
    "querystring": set(),
    "header": {"simple"},
    "cookie": {"form"},
}


def parse_openapi(document: Mapping[str, Any]) -> OpenAPIIR:
    """Validate and translate an OpenAPI 3.0/3.1 document into stable IR."""
    version = str(document.get("openapi", ""))
    if not version.startswith(("3.0.", "3.1.", "3.2.")):
        raise UnsupportedOpenAPIError("#/openapi", "expected OpenAPI 3.0, 3.1 or 3.2")
    _validate_extension_schema(document)
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise UnsupportedOpenAPIError("#/paths", "paths must be an object")
    operations: list[OperationIR] = []
    model_names: set[str] = set()
    root_security = document.get("security")
    root_servers = document.get("servers")
    root_extension = _extension(document.get("x-eazy-sdk"), "#/x-eazy-sdk")
    dependencies = _dependencies(root_extension.get("dependencies", {}), model_names)
    dependency_names = {dependency.name for dependency in dependencies}
    protection_flows = _protection_flows(root_extension.get("protectionFlows", {}), model_names)
    protection_names = {flow.name for flow in protection_flows}
    domain_rules = _domain_rules(root_extension.get("domainRules", ()), dependency_names)
    signature_profiles = _signatures(root_extension.get("signatureProfiles", {}))
    known_signatures = {item.name for item in signature_profiles}
    security_schemes = _security_schemes(document)
    known_security = {item.name for item in security_schemes}
    for raw_path, raw_path_item in paths.items():
        pointer = f"#/paths/{_escape_pointer(str(raw_path))}"
        path_item = _resolve(document, raw_path_item, pointer)
        if not isinstance(path_item, Mapping):
            raise UnsupportedOpenAPIError(pointer, "path item must be an object")
        path_parameters = _parameter_list(document, path_item.get("parameters", ()), pointer)
        for method in _METHODS:
            if method not in path_item:
                continue
            operation_pointer = f"{pointer}/{method}"
            operation = _resolve(document, path_item[method], operation_pointer)
            if not isinstance(operation, Mapping):
                raise UnsupportedOpenAPIError(operation_pointer, "operation must be an object")
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                raise UnsupportedOpenAPIError(
                    f"{operation_pointer}/operationId",
                    "operationId is required for stable generated names",
                )
            own_parameters = _parameter_list(
                document, operation.get("parameters", ()), operation_pointer, operation_id
            )
            parameters = _merge_parameters(path_parameters, own_parameters)
            query_count = sum(p.location == "query" for p in parameters)
            querystring_count = sum(p.location == "querystring" for p in parameters)
            if querystring_count > 1 or (querystring_count and query_count):
                raise UnsupportedOpenAPIError(
                    f"{operation_pointer}/parameters",
                    "querystring appears at most once and cannot coexist with query parameters",
                    operation_id=operation_id,
                )
            for parameter in parameters:
                _collect_model(parameter.type_expression, model_names)
            body = _request_body(document, operation, operation_pointer, operation_id, model_names)
            responses = _responses(
                document, operation, operation_pointer, operation_id, model_names
            )
            security_raw = operation.get("security", root_security)
            security = _security(security_raw, operation_pointer, operation_id)
            if security is not None:
                for alternative in security:
                    for scheme_name, _ in alternative.schemes:
                        if scheme_name not in known_security:
                            raise UnsupportedOpenAPIError(
                                f"{operation_pointer}/security",
                                f"unknown security scheme {scheme_name!r}",
                                operation_id=operation_id,
                            )
            tags_raw = operation.get("tags", ())
            if not isinstance(tags_raw, Sequence) or isinstance(tags_raw, str | bytes):
                raise UnsupportedOpenAPIError(
                    f"{operation_pointer}/tags", "tags must be an array", operation_id=operation_id
                )
            server = _server(operation.get("servers", root_servers), operation_pointer)
            extension = _extension(
                operation.get("x-eazy-sdk", {}), f"{operation_pointer}/x-eazy-sdk"
            )
            body_projection = _body_projection(
                extension.get("projection"),
                document,
                operation,
                body,
                operation_pointer,
                operation_id,
                model_names,
            )
            requirements = _dependency_requirements(
                extension.get("requires", ()),
                f"{operation_pointer}/x-eazy-sdk/requires",
                dependency_names,
                operation_id=operation_id,
            )
            wire = _wire_options(
                extension.get("wire"),
                f"{operation_pointer}/x-eazy-sdk/wire",
                operation_id,
            )
            replay = extension.get("replay", {})
            if not isinstance(replay, Mapping):
                raise UnsupportedOpenAPIError(
                    f"{operation_pointer}/x-eazy-sdk/replay",
                    "replay must be an object",
                    operation_id=operation_id,
                )
            idempotent_raw = replay.get("idempotent")
            if idempotent_raw is not None and not isinstance(idempotent_raw, bool):
                raise UnsupportedOpenAPIError(
                    f"{operation_pointer}/x-eazy-sdk/replay/idempotent",
                    "idempotent must be a boolean",
                    operation_id=operation_id,
                )
            signature_uses = _reference_names(
                extension.get("signatures", ()),
                f"{operation_pointer}/x-eazy-sdk/signatures",
            )
            _ensure_known(signature_uses, known_signatures, "signature", operation_pointer)
            protection_uses = _protection_uses(
                extension.get("protections", ()),
                f"{operation_pointer}/x-eazy-sdk/protections",
                protection_names,
                protection_flows,
                body,
                document,
                operation_id,
            )
            crypto = _crypto_use(
                operation.get("x-eazy-sdk-crypto"),
                f"{operation_pointer}/x-eazy-sdk-crypto",
                operation_id,
            )
            operations.append(
                OperationIR(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=str(raw_path),
                    parameters=parameters,
                    request_body=body,
                    responses=responses,
                    security=security,
                    tags=tuple(str(tag) for tag in tags_raw),
                    server=server,
                    requires=requirements,
                    signatures=signature_uses,
                    wire=wire,
                    idempotent=idempotent_raw,
                    protections=protection_uses,
                    body_projection=body_projection,
                    crypto=crypto,
                )
            )
    _validate_protection_operations(protection_flows, tuple(operations))
    session_auth = _session_auth(
        root_extension.get("sessionAuth"),
        document,
        tuple(operations),
        security_schemes,
    )
    if session_auth is not None:
        model_names.update(
            {
                session_auth.session_model,
                session_auth.credentials_model,
                session_auth.acquire.request_model,
                *(
                    (session_auth.refresh.request_model,)
                    if session_auth.refresh is not None
                    else ()
                ),
            }
        )
    return OpenAPIIR(
        version,
        tuple(operations),
        frozenset(model_names),
        dependencies,
        domain_rules,
        _index_references(document),
        security_schemes,
        signature_profiles,
        session_auth,
        protection_flows,
    )


def _protection_flows(raw: Any, model_names: set[str]) -> tuple[ProtectionFlowIR, ...]:
    pointer = "#/x-eazy-sdk/protectionFlows"
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError(pointer, "must be an object")
    flows: list[ProtectionFlowIR] = []
    for raw_name, value in raw.items():
        name = str(raw_name)
        item_pointer = f"{pointer}/{_escape_pointer(name)}"
        if not isinstance(value, Mapping):
            raise UnsupportedOpenAPIError(item_pointer, "flow must be an object")
        unknown = set(value) - {"result", "acquire", "solve", "verify"}
        if unknown:
            raise UnsupportedOpenAPIError(item_pointer, f"unknown fields: {sorted(unknown)}")
        acquire = value.get("acquire")
        if not isinstance(acquire, str) or not acquire:
            raise UnsupportedOpenAPIError(f"{item_pointer}/acquire", "must be an operationId")
        solve = value.get("solve", False)
        if not isinstance(solve, bool):
            raise UnsupportedOpenAPIError(f"{item_pointer}/solve", "must be a boolean")
        verify = value.get("verify")
        if verify is not None and (not isinstance(verify, str) or not verify):
            raise UnsupportedOpenAPIError(
                f"{item_pointer}/verify", "must be an operationId or null"
            )
        if verify is not None and not solve:
            raise UnsupportedOpenAPIError(f"{item_pointer}/verify", "verify requires solve=true")
        result_type = _schema_type(value.get("result"), f"{item_pointer}/result", None)
        _collect_model(result_type, model_names)
        flows.append(ProtectionFlowIR(name, result_type, acquire, solve, verify))
    return tuple(flows)


def _protection_uses(
    raw: Any,
    pointer: str,
    known: set[str],
    flows: tuple[ProtectionFlowIR, ...],
    body: RequestBodyIR | None,
    document: Mapping[str, Any],
    operation_id: str,
) -> tuple[ProtectionUseIR, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise UnsupportedOpenAPIError(pointer, "must be an array", operation_id=operation_id)
    available_targets = _request_body_field_names(body, document)
    by_name = {flow.name: flow for flow in flows}
    uses: list[ProtectionUseIR] = []
    claimed: set[str] = set()
    for index, value in enumerate(raw):
        item_pointer = f"{pointer}/{index}"
        if not isinstance(value, Mapping):
            raise UnsupportedOpenAPIError(
                item_pointer, "must be an object", operation_id=operation_id
            )
        unknown = set(value) - {"flow", "outputs"}
        if unknown:
            raise UnsupportedOpenAPIError(
                item_pointer,
                f"unknown fields: {sorted(unknown)}",
                operation_id=operation_id,
            )
        flow_name = value.get("flow")
        if not isinstance(flow_name, str) or flow_name not in known:
            raise UnsupportedOpenAPIError(
                f"{item_pointer}/flow",
                f"unknown protection flow {flow_name!r}",
                operation_id=operation_id,
            )
        outputs = value.get("outputs")
        if not isinstance(outputs, Mapping) or not outputs:
            raise UnsupportedOpenAPIError(
                f"{item_pointer}/outputs",
                "must be a non-empty target-to-source object",
                operation_id=operation_id,
            )
        flow = by_name[flow_name]
        source_fields = _model_field_names(document, flow.result_type)
        mappings: list[ProtectionOutputIR] = []
        for raw_target, raw_source in outputs.items():
            target = str(raw_target)
            if not isinstance(raw_source, str) or not raw_source:
                raise UnsupportedOpenAPIError(
                    f"{item_pointer}/outputs/{_escape_pointer(target)}",
                    "source must be a non-empty result field name",
                    operation_id=operation_id,
                )
            if target not in available_targets:
                raise UnsupportedOpenAPIError(
                    f"{item_pointer}/outputs/{_escape_pointer(target)}",
                    "target is not a request-body field",
                    operation_id=operation_id,
                )
            if target in claimed:
                raise UnsupportedOpenAPIError(
                    f"{item_pointer}/outputs/{_escape_pointer(target)}",
                    "target is mapped more than once",
                    operation_id=operation_id,
                )
            if raw_source not in source_fields:
                raise UnsupportedOpenAPIError(
                    f"{item_pointer}/outputs/{_escape_pointer(target)}",
                    f"protection result has no field {raw_source!r}",
                    operation_id=operation_id,
                )
            claimed.add(target)
            mappings.append(ProtectionOutputIR(target, raw_source))
        uses.append(ProtectionUseIR(flow_name, tuple(mappings)))
    if uses and body is None:
        raise UnsupportedOpenAPIError(
            pointer, "protections require a request body", operation_id=operation_id
        )
    return tuple(uses)


def _request_body_field_names(
    body: RequestBodyIR | None, document: Mapping[str, Any]
) -> frozenset[str]:
    if body is None:
        return frozenset()
    if body.fields is not None:
        return frozenset(field.name for field in body.fields)
    return _model_field_names(document, body.type_expression)


def _model_field_names(document: Mapping[str, Any], model: str) -> frozenset[str]:
    components = document.get("components", {})
    schemas = components.get("schemas", {}) if isinstance(components, Mapping) else {}
    schema = schemas.get(model) if isinstance(schemas, Mapping) else None
    properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
    if not isinstance(properties, Mapping):
        return frozenset()
    return frozenset(str(name) for name in properties)


def _validate_protection_operations(
    flows: tuple[ProtectionFlowIR, ...], operations: tuple[OperationIR, ...]
) -> None:
    by_id = {operation.operation_id: operation for operation in operations}
    for flow in flows:
        pointer = f"#/x-eazy-sdk/protectionFlows/{_escape_pointer(flow.name)}"
        acquire = by_id.get(flow.acquire)
        if acquire is None:
            raise UnsupportedOpenAPIError(
                f"{pointer}/acquire", f"unknown operation {flow.acquire!r}"
            )
        if acquire.parameters or acquire.request_body is not None:
            raise UnsupportedOpenAPIError(
                f"{pointer}/acquire", "acquire operation must not require input"
            )
        if flow.verify is not None:
            verify = by_id.get(flow.verify)
            if verify is None:
                raise UnsupportedOpenAPIError(
                    f"{pointer}/verify", f"unknown operation {flow.verify!r}"
                )
            count = len(verify.parameters) + (verify.request_body is not None)
            if count != 1:
                raise UnsupportedOpenAPIError(
                    f"{pointer}/verify", "verify operation must have exactly one input"
                )


def _session_auth(
    raw: Any,
    document: Mapping[str, Any],
    operations: tuple[OperationIR, ...],
    security_schemes: tuple[SecuritySchemeIR, ...],
) -> SessionAuthIR | None:
    if raw is None:
        return None
    pointer = "#/x-eazy-sdk/sessionAuth"
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError(pointer, "sessionAuth must be an object")
    allowed = {
        "scheme",
        "sessionModel",
        "credentialsModel",
        "bearerField",
        "refreshTokenField",
        "expiresAtField",
        "expiresLeewaySeconds",
        "acquire",
        "refresh",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise UnsupportedOpenAPIError(pointer, f"unknown fields: {sorted(unknown)}")

    def required_string(name: str) -> str:
        value = raw.get(name)
        if not isinstance(value, str) or not value:
            raise UnsupportedOpenAPIError(f"{pointer}/{name}", "must be a non-empty string")
        return value

    scheme = required_string("scheme")
    if scheme not in {item.name for item in security_schemes}:
        raise UnsupportedOpenAPIError(f"{pointer}/scheme", f"unknown scheme {scheme!r}")
    selected_scheme = next(item for item in security_schemes if item.name == scheme)
    if selected_scheme.scheme != "bearer":
        raise UnsupportedOpenAPIError(
            f"{pointer}/scheme", "managed JSON sessions require a bearer security scheme"
        )
    session_model = required_string("sessionModel")
    credentials_model = required_string("credentialsModel")
    bearer_field = required_string("bearerField")
    refresh_token_field = _optional_string(raw, "refreshTokenField", pointer)
    expires_at_field = _optional_string(raw, "expiresAtField", pointer)
    leeway = raw.get("expiresLeewaySeconds", 30)
    if not isinstance(leeway, int | float) or isinstance(leeway, bool) or leeway < 0:
        raise UnsupportedOpenAPIError(
            f"{pointer}/expiresLeewaySeconds", "must be a non-negative number"
        )
    _require_schema_fields(
        document,
        session_model,
        tuple(
            field
            for field in (bearer_field, refresh_token_field, expires_at_field)
            if field is not None
        ),
        pointer,
    )
    _require_schema_fields(document, credentials_model, (), pointer)
    by_operation = {item.operation_id: item for item in operations}
    acquire = _session_call(raw.get("acquire"), f"{pointer}/acquire", by_operation)
    refresh_raw = raw.get("refresh")
    refresh = (
        _session_call(refresh_raw, f"{pointer}/refresh", by_operation)
        if refresh_raw is not None
        else None
    )
    if refresh is not None and refresh_token_field is None:
        raise UnsupportedOpenAPIError(
            f"{pointer}/refreshTokenField", "is required when refresh is configured"
        )
    return SessionAuthIR(
        scheme,
        session_model,
        credentials_model,
        bearer_field,
        refresh_token_field,
        expires_at_field,
        float(leeway),
        acquire,
        refresh,
    )


def _optional_string(raw: Mapping[str, Any], name: str, pointer: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise UnsupportedOpenAPIError(f"{pointer}/{name}", "must be a non-empty string")
    return value


def _require_schema_fields(
    document: Mapping[str, Any],
    model: str,
    fields: tuple[str, ...],
    pointer: str,
) -> None:
    components = document.get("components", {})
    schemas = components.get("schemas", {}) if isinstance(components, Mapping) else {}
    schema = schemas.get(model) if isinstance(schemas, Mapping) else None
    if not isinstance(schema, Mapping):
        raise UnsupportedOpenAPIError(pointer, f"unknown component schema {model!r}")
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        properties = {}
    missing = [field for field in fields if field not in properties]
    if missing:
        raise UnsupportedOpenAPIError(pointer, f"session model {model!r} has no fields {missing!r}")


def _session_call(
    raw: Any,
    pointer: str,
    operations: Mapping[str, OperationIR],
) -> SessionCallIR:
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError(pointer, "must be an object")
    allowed = {"operation", "requestField", "requestModel", "fields"}
    unknown = set(raw) - allowed
    if unknown:
        raise UnsupportedOpenAPIError(pointer, f"unknown fields: {sorted(unknown)}")
    strings: dict[str, str] = {}
    for name in ("operation", "requestField", "requestModel"):
        value = raw.get(name)
        if not isinstance(value, str) or not value:
            raise UnsupportedOpenAPIError(f"{pointer}/{name}", "must be a non-empty string")
        strings[name] = value
    operation = operations.get(strings["operation"])
    if operation is None:
        raise UnsupportedOpenAPIError(
            f"{pointer}/operation", f"unknown operation {strings['operation']!r}"
        )
    if (
        operation.request_body is None
        or operation.request_body.type_expression != strings["requestModel"]
    ):
        raise UnsupportedOpenAPIError(
            pointer, "requestModel must match the operation request body model"
        )
    fields = raw.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        raise UnsupportedOpenAPIError(f"{pointer}/fields", "must be a non-empty object")
    values: list[SessionValueIR] = []
    for target, value in fields.items():
        if not isinstance(target, str) or not target:
            raise UnsupportedOpenAPIError(f"{pointer}/fields", "target names must be strings")
        if isinstance(value, str) and value:
            values.append(SessionValueIR(target, source=value))
        elif isinstance(value, Mapping) and set(value) == {"literal"}:
            values.append(SessionValueIR(target, literal=value["literal"], is_literal=True))
        else:
            raise UnsupportedOpenAPIError(
                f"{pointer}/fields/{_escape_pointer(target)}",
                "must be an attribute path or {'literal': value}",
            )
    return SessionCallIR(
        strings["operation"],
        strings["requestField"],
        strings["requestModel"],
        tuple(values),
    )


def _security_schemes(document: Mapping[str, Any]) -> tuple[SecuritySchemeIR, ...]:
    components = document.get("components", {})
    raw = components.get("securitySchemes", {}) if isinstance(components, Mapping) else {}
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError("#/components/securitySchemes", "must be an object")
    output: list[SecuritySchemeIR] = []
    for raw_name, value in raw.items():
        name = str(raw_name)
        pointer = f"#/components/securitySchemes/{_escape_pointer(name)}"
        if not isinstance(value, Mapping):
            raise UnsupportedOpenAPIError(pointer, "security scheme must be an object")
        kind = value.get("type")
        if kind == "apiKey":
            location = value.get("in")
            wire_name = value.get("name")
            if location not in {"header", "query", "cookie"} or not isinstance(wire_name, str):
                raise UnsupportedOpenAPIError(pointer, "apiKey requires supported in/name")
            output.append(SecuritySchemeIR(name, "apiKey", location, wire_name))
        elif kind == "http" and value.get("scheme") in {"bearer", "basic"}:
            output.append(SecuritySchemeIR(name, "http", scheme=str(value["scheme"])))
        elif kind in {"oauth2", "openIdConnect"}:
            # OpenAPI describes how an application obtains a token, while the generated
            # runtime contract only injects an already acquired bearer token. The omitted
            # acquisition flow is made explicit by ``analyze_openapi``.
            output.append(SecuritySchemeIR(name, str(kind), scheme="bearer"))
        else:
            raise UnsupportedOpenAPIError(pointer, "unsupported security scheme")
    return tuple(output)


def _wire_options(raw: Any, pointer: str, operation_id: str) -> WireOptionsIR | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError(pointer, "wire must be an object", operation_id=operation_id)

    def order(name: str) -> tuple[str, ...] | None:
        value = raw.get(name)
        if value is None:
            return None
        if (
            not isinstance(value, Sequence)
            or isinstance(value, str | bytes)
            or not all(isinstance(item, str) for item in value)
        ):
            raise UnsupportedOpenAPIError(
                f"{pointer}/{name}", "order must be an array of strings", operation_id=operation_id
            )
        result = tuple(value)
        if len(set(result)) != len(result):
            raise UnsupportedOpenAPIError(
                f"{pointer}/{name}", "order contains duplicates", operation_id=operation_id
            )
        return result

    protocol = raw.get("protocol")
    if protocol is not None and protocol not in {"http/1.1", "http/2", "http/3"}:
        raise UnsupportedOpenAPIError(
            f"{pointer}/protocol", "unsupported protocol", operation_id=operation_id
        )
    return WireOptionsIR(
        order("queryOrder"),
        order("headerOrder"),
        order("cookieOrder"),
        order("bodyOrder"),
        bool(raw.get("exact", False)),
        protocol,
    )


def _crypto_use(raw: Any, pointer: str, operation_id: str) -> CryptoUseIR | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError(
            pointer,
            "crypto must be an object",
            operation_id=operation_id,
        )
    unknown = set(raw) - {"profile", "direction", "wire"}
    if unknown:
        raise UnsupportedOpenAPIError(
            pointer,
            f"unknown crypto fields: {sorted(unknown)}",
            operation_id=operation_id,
        )
    profile = raw.get("profile")
    if not isinstance(profile, str) or not profile:
        raise UnsupportedOpenAPIError(
            f"{pointer}/profile",
            "crypto profile must be a non-empty string",
            operation_id=operation_id,
        )
    direction = raw.get("direction", "bidirectional")
    if direction not in {"outbound", "inbound", "bidirectional"}:
        raise UnsupportedOpenAPIError(
            f"{pointer}/direction",
            "crypto direction must be outbound, inbound or bidirectional",
            operation_id=operation_id,
        )
    wire_raw = raw.get("wire", {})
    if not isinstance(wire_raw, Mapping):
        raise UnsupportedOpenAPIError(
            f"{pointer}/wire",
            "crypto wire must be an object",
            operation_id=operation_id,
        )
    wire_unknown = set(wire_raw) - {
        "contentType",
        "clearContentType",
        "plaintextStatuses",
    }
    if wire_unknown:
        raise UnsupportedOpenAPIError(
            f"{pointer}/wire",
            f"unknown crypto wire fields: {sorted(wire_unknown)}",
            operation_id=operation_id,
        )
    content_type = wire_raw.get("contentType", "application/octet-stream")
    clear_content_type = wire_raw.get("clearContentType", "application/json")
    if not isinstance(content_type, str) or "/" not in content_type:
        raise UnsupportedOpenAPIError(
            f"{pointer}/wire/contentType",
            "contentType must be a media type string",
            operation_id=operation_id,
        )
    if not isinstance(clear_content_type, str) or "/" not in clear_content_type:
        raise UnsupportedOpenAPIError(
            f"{pointer}/wire/clearContentType",
            "clearContentType must be a media type string",
            operation_id=operation_id,
        )
    statuses = wire_raw.get("plaintextStatuses", ())
    if not isinstance(statuses, Sequence) or isinstance(statuses, str | bytes):
        raise UnsupportedOpenAPIError(
            f"{pointer}/wire/plaintextStatuses",
            "plaintextStatuses must be an array",
            operation_id=operation_id,
        )
    if not all(isinstance(status, int) and 100 <= status <= 599 for status in statuses):
        raise UnsupportedOpenAPIError(
            f"{pointer}/wire/plaintextStatuses",
            "plaintextStatuses must contain valid HTTP status codes",
            operation_id=operation_id,
        )
    return CryptoUseIR(
        profile,
        cast(str, direction),
        CryptoWireIR(content_type, clear_content_type, tuple(statuses)),
    )


def _validate_extension_schema(document: Mapping[str, Any]) -> None:
    legacy = {
        "x-eazy-sdk-dependency-rules",
        "x-eazy-sdk-requires",
        "x-eazy-sdk-request-dependencies",
    }
    allowed_root = {
        "dependencies",
        "protectionFlows",
        "signatureProfiles",
        "domainRules",
        "sessionAuth",
    }
    extension = document.get("x-eazy-sdk")
    if extension is not None:
        if not isinstance(extension, Mapping):
            raise UnsupportedOpenAPIError("#/x-eazy-sdk", "extension must be an object")
        unknown = set(extension) - allowed_root
        if unknown:
            raise UnsupportedOpenAPIError("#/x-eazy-sdk", f"unknown fields: {sorted(unknown)}")
    if legacy & set(document):
        raise UnsupportedOpenAPIError("#", "legacy Eazy SDK extensions are not supported")
    components = document.get("components", {})
    if isinstance(components, Mapping) and legacy & set(components):
        raise UnsupportedOpenAPIError(
            "#/components", "legacy Eazy SDK extensions are not supported"
        )
    paths = document.get("paths", {})
    if isinstance(paths, Mapping):
        for raw_path, item in paths.items():
            if not isinstance(item, Mapping):
                continue
            for method in _METHODS:
                operation = item.get(method)
                if isinstance(operation, Mapping) and legacy & set(operation):
                    raise UnsupportedOpenAPIError(
                        f"#/paths/{_escape_pointer(str(raw_path))}/{method}",
                        "legacy Eazy SDK extensions are not supported",
                        operation_id=operation.get("operationId")
                        if isinstance(operation.get("operationId"), str)
                        else None,
                    )
                if isinstance(operation, Mapping) and "x-eazy-sdk" in operation:
                    value = operation["x-eazy-sdk"]
                    if not isinstance(value, Mapping):
                        raise UnsupportedOpenAPIError(
                            f"#/paths/{_escape_pointer(str(raw_path))}/{method}/x-eazy-sdk",
                            "extension must be an object",
                        )
                    allowed = {
                        "requires",
                        "wire",
                        "signatures",
                        "replay",
                        "protections",
                        "projection",
                    }
                    unknown = set(value) - allowed
                    if unknown:
                        raise UnsupportedOpenAPIError(
                            f"#/paths/{_escape_pointer(str(raw_path))}/{method}/x-eazy-sdk",
                            f"unknown fields: {sorted(unknown)}",
                            operation_id=operation.get("operationId")
                            if isinstance(operation.get("operationId"), str)
                            else None,
                        )


def _index_references(document: Mapping[str, Any]) -> tuple[Ref[Any], ...]:
    interned: dict[str, Ref[Any]] = {}

    def resolve(reference: str) -> Any:
        current: Any = document
        for part in reference[2:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or key not in current:
                raise UnsupportedOpenAPIError(reference, f"unresolvable $ref {reference!r}")
            current = current[key]
        return current

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            reference = value.get("$ref")
            if isinstance(reference, str):
                if not reference.startswith("#/"):
                    raise UnsupportedOpenAPIError("#", "only local $ref values are supported")
                if reference not in interned:
                    interned[reference] = Ref(
                        SourceIdentity("<document>", reference), resolve(reference)
                    )
            for child in value.values():
                visit(child)
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
            for child in value:
                visit(child)

    visit(document)
    return tuple(interned[key] for key in sorted(interned))


def _extension(value: Any, pointer: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise UnsupportedOpenAPIError(pointer, "extension must be an object")
    return value


def _dependencies(raw: Any, model_names: set[str]) -> tuple[DependencyIR, ...]:
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError("#/x-eazy-sdk/dependencies", "must be an object")
    output: list[DependencyIR] = []
    for raw_name, raw_dependency in raw.items():
        name = str(raw_name)
        pointer = f"#/x-eazy-sdk/dependencies/{_escape_pointer(name)}"
        if not isinstance(raw_dependency, Mapping):
            raise UnsupportedOpenAPIError(pointer, "dependency must be an object")
        result_type = _schema_type(raw_dependency.get("result"), f"{pointer}/result", None)
        _collect_model(result_type, model_names)
        cache = str(raw_dependency.get("cache", "call")).replace("per_", "")
        if cache not in {"none", "client", "call", "attempt"}:
            raise UnsupportedOpenAPIError(
                f"{pointer}/cache", "expected none, client, call or attempt"
            )
        raw_bindings = raw_dependency.get("bindings")
        if not isinstance(raw_bindings, Sequence) or isinstance(raw_bindings, str | bytes):
            raise UnsupportedOpenAPIError(f"{pointer}/bindings", "bindings must be an array")
        bindings: list[DependencyBindingIR] = []
        for index, raw_binding in enumerate(raw_bindings):
            binding_pointer = f"{pointer}/bindings/{index}"
            if not isinstance(raw_binding, Mapping):
                raise UnsupportedOpenAPIError(binding_pointer, "binding must be an object")
            location = str(raw_binding.get("in", ""))
            if location not in {"header", "cookie", "query", "body"}:
                raise UnsupportedOpenAPIError(
                    f"{binding_pointer}/in", "expected header, cookie, query or body"
                )
            target_name = raw_binding.get("name")
            if not isinstance(target_name, str) or not target_name:
                raise UnsupportedOpenAPIError(
                    f"{binding_pointer}/name", "binding name must be a non-empty string"
                )
            raw_field = raw_binding.get("field")
            if raw_field is not None and not isinstance(raw_field, str):
                raise UnsupportedOpenAPIError(
                    f"{binding_pointer}/field", "field must be a string or null"
                )
            bindings.append(
                DependencyBindingIR(
                    field=raw_field,
                    location=location,
                    name=target_name,
                    required=bool(raw_binding.get("required", True)),
                )
            )
        if not bindings:
            raise UnsupportedOpenAPIError(f"{pointer}/bindings", "at least one binding is required")
        output.append(
            DependencyIR(
                name, result_type, cache, bool(raw_dependency.get("secret", False)), tuple(bindings)
            )
        )
    return tuple(output)


def _dependency_ref(raw: Any, pointer: str) -> str:
    if isinstance(raw, str) and not raw.startswith("#/"):
        return raw
    if not isinstance(raw, Mapping) or not isinstance(raw.get("$ref"), str):
        raise UnsupportedOpenAPIError(pointer, "dependency must be a name or local $ref")
    ref = str(raw["$ref"])
    prefix = "#/x-eazy-sdk/dependencies/"
    if not ref.startswith(prefix):
        raise UnsupportedOpenAPIError(pointer, "dependency $ref targets the wrong component")
    return ref.removeprefix(prefix).replace("~1", "/").replace("~0", "~")


def _dependency_requirements(
    raw: Any,
    pointer: str,
    known: set[str],
    *,
    operation_id: str | None = None,
) -> tuple[DependencyRequirementIR, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise UnsupportedOpenAPIError(pointer, "dependency uses must be an array")
    output: list[DependencyRequirementIR] = []
    for index, item in enumerate(raw):
        item_pointer = f"{pointer}/{index}"
        source = item.get("dependency") if isinstance(item, Mapping) else item
        name = _dependency_ref(source, f"{item_pointer}/dependency")
        if name not in known:
            raise UnsupportedOpenAPIError(
                f"{item_pointer}/dependency",
                f"unknown dependency {name!r}",
                operation_id=operation_id,
            )
        required = item.get("required", True) if isinstance(item, Mapping) else True
        if not isinstance(required, bool):
            raise UnsupportedOpenAPIError(f"{item_pointer}/required", "must be a boolean")
        output.append(DependencyRequirementIR(name, required))
    return tuple(output)


def _domain_rules(raw: Any, known: set[str]) -> tuple[DomainRuleIR, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise UnsupportedOpenAPIError("#/x-eazy-sdk/domainRules", "must be an array")
    output: list[DomainRuleIR] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise UnsupportedOpenAPIError(f"#/x-eazy-sdk/domainRules/{index}", "must be an object")
        unknown = set(item) - {"scope", "requires", "wireProfile"}
        if unknown:
            raise UnsupportedOpenAPIError(
                f"#/x-eazy-sdk/domainRules/{index}",
                f"unknown fields: {sorted(unknown)}",
            )
        scope_raw = item.get("scope", {})
        pointer = f"#/x-eazy-sdk/domainRules/{index}/scope"
        if not isinstance(scope_raw, Mapping):
            raise UnsupportedOpenAPIError(pointer, "scope must be an object")
        requires = _dependency_requirements(
            item.get("requires", ()), f"#/x-eazy-sdk/domainRules/{index}/requires", known
        )
        output.append(
            DomainRuleIR(
                ScopeIR(
                    hosts=_scope_strings(scope_raw, "hosts", pointer),
                    domains=_scope_strings(scope_raw, "domains", pointer),
                    schemes=_scope_strings(scope_raw, "schemes", pointer),
                    methods=_scope_strings(scope_raw, "methods", pointer),
                    path_prefixes=_scope_strings(scope_raw, "pathPrefixes", pointer),
                    path_patterns=_scope_strings(scope_raw, "pathPatterns", pointer),
                    endpoint_names=_scope_strings(scope_raw, "endpointNames", pointer),
                    tags=_scope_strings(scope_raw, "tags", pointer),
                ),
                requires,
                str(item["wireProfile"]) if "wireProfile" in item else None,
            )
        )
    return tuple(output)


def _named_objects(raw: Any, pointer: str) -> list[tuple[str, Mapping[str, Any]]]:
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError(pointer, "must be an object")
    output: list[tuple[str, Mapping[str, Any]]] = []
    for name, value in raw.items():
        if not isinstance(value, Mapping):
            raise UnsupportedOpenAPIError(
                f"{pointer}/{_escape_pointer(str(name))}", "must be an object"
            )
        output.append((str(name), value))
    return output


def _required_string(value: Mapping[str, Any], key: str, pointer: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise UnsupportedOpenAPIError(f"{pointer}/{key}", "must be a non-empty string")
    return result


def _signatures(raw: Any) -> tuple[SignatureIR, ...]:
    output: list[SignatureIR] = []
    for name, value in _named_objects(raw, "#/x-eazy-sdk/signatureProfiles"):
        pointer = f"#/x-eazy-sdk/signatureProfiles/{_escape_pointer(name)}"
        implementation = _required_string(value, "implementation", pointer)
        if ":" not in implementation or any(
            token in implementation for token in ("lambda", "(", ")")
        ):
            raise UnsupportedOpenAPIError(f"{pointer}/implementation", "expected module:constant")
        output.append(SignatureIR(name, implementation))
    return tuple(output)


def _reference_names(raw: Any, pointer: str) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise UnsupportedOpenAPIError(pointer, "must be an array")
    output: list[str] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            output.append(item.rsplit("/", 1)[-1] if item.startswith("#/") else item)
        elif isinstance(item, Mapping) and isinstance(item.get("$ref"), str):
            output.append(str(item["$ref"]).rsplit("/", 1)[-1])
        else:
            raise UnsupportedOpenAPIError(f"{pointer}/{index}", "must be a name or local $ref")
    return tuple(output)


def _ensure_known(
    values: tuple[str, ...], known: set[str], kind: str, operation_pointer: str
) -> None:
    unknown = sorted(set(values) - known)
    if unknown:
        raise UnsupportedOpenAPIError(
            f"{operation_pointer}/x-eazy-sdk/{kind}s", f"unknown {kind}s: {unknown}"
        )


def _scope_strings(scope: Mapping[str, Any], key: str, pointer: str) -> tuple[str, ...]:
    value = scope.get(key, ())
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise UnsupportedOpenAPIError(f"{pointer}/{key}", f"{key} must be an array")
    return tuple(str(item) for item in value)


def _resolve(document: Mapping[str, Any], value: Any, pointer: str) -> Any:
    if not isinstance(value, Mapping) or "$ref" not in value:
        return value
    ref = value["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise UnsupportedOpenAPIError(pointer, "only local $ref values are supported")
    current: Any = document
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or key not in current:
            raise UnsupportedOpenAPIError(pointer, f"unresolvable $ref {ref!r}")
        current = current[key]
    return current


def _parameter_list(
    document: Mapping[str, Any],
    raw: Any,
    pointer: str,
    operation_id: str | None = None,
) -> tuple[ParameterIR, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise UnsupportedOpenAPIError(
            f"{pointer}/parameters", "parameters must be an array", operation_id=operation_id
        )
    out: list[ParameterIR] = []
    for index, item in enumerate(raw):
        item_pointer = f"{pointer}/parameters/{index}"
        parameter = _resolve(document, item, item_pointer)
        if not isinstance(parameter, Mapping):
            raise UnsupportedOpenAPIError(
                item_pointer, "parameter must be an object", operation_id=operation_id
            )
        location = parameter.get("in")
        name = parameter.get("name")
        if location not in _LOCATION_DEFAULTS or not isinstance(name, str):
            raise UnsupportedOpenAPIError(
                item_pointer,
                "parameter needs a supported 'in' and string 'name'",
                operation_id=operation_id,
            )
        default_style, default_explode = _LOCATION_DEFAULTS[location]
        if location == "querystring":
            if not version_supports_querystring(document):
                raise UnsupportedOpenAPIError(
                    item_pointer,
                    "querystring requires OpenAPI 3.2",
                    operation_id=operation_id,
                )
            content = parameter.get("content")
            if not isinstance(content, Mapping) or len(content) != 1:
                raise UnsupportedOpenAPIError(
                    f"{item_pointer}/content",
                    "querystring requires exactly one media type",
                    operation_id=operation_id,
                )
            media_type, representation = next(iter(content.items()))
            if media_type not in {"application/x-www-form-urlencoded", "text/plain"}:
                raise UnsupportedOpenAPIError(
                    f"{item_pointer}/content/{media_type}",
                    "only form-urlencoded and text/plain query strings are supported",
                    operation_id=operation_id,
                )
            schema = representation.get("schema", {}) if isinstance(representation, Mapping) else {}
            expression = _schema_type(schema, item_pointer, operation_id)
            out.append(
                ParameterIR(
                    name=name or "querystring",
                    location=location,
                    type_expression=expression,
                    required=bool(parameter.get("required", False)),
                    style="",
                    explode=False,
                    allow_reserved=False,
                    content_type=str(media_type),
                )
            )
            continue
        style = str(parameter.get("style", default_style))
        if style not in _STYLES[location]:
            raise UnsupportedOpenAPIError(
                f"{item_pointer}/style",
                f"style {style!r} is unsupported for {location}",
                operation_id=operation_id,
            )
        required = bool(parameter.get("required", False))
        if location == "path" and not required:
            raise UnsupportedOpenAPIError(
                f"{item_pointer}/required",
                "path parameters must be required",
                operation_id=operation_id,
            )
        schema = parameter.get("schema", {})
        expression = _schema_type(schema, item_pointer, operation_id)
        explode = bool(parameter.get("explode", default_explode))
        if location == "query" and style == "form" and explode and expression.startswith("list["):
            raise UnsupportedOpenAPIError(
                f"{item_pointer}/explode",
                "query arrays with explode=true require repeated keys, which are unsupported",
                operation_id=operation_id,
            )
        out.append(
            ParameterIR(
                name=name,
                location=location,
                type_expression=expression,
                required=required,
                style=style,
                explode=explode,
                allow_reserved=bool(parameter.get("allowReserved", False)),
            )
        )
    return tuple(out)


def _merge_parameters(
    path_parameters: tuple[ParameterIR, ...], operation_parameters: tuple[ParameterIR, ...]
) -> tuple[ParameterIR, ...]:
    merged = {(parameter.location, parameter.name): parameter for parameter in path_parameters}
    merged.update(
        {(parameter.location, parameter.name): parameter for parameter in operation_parameters}
    )
    return tuple(merged.values())


def _request_body(
    document: Mapping[str, Any],
    operation: Mapping[str, Any],
    pointer: str,
    operation_id: str,
    model_names: set[str],
) -> RequestBodyIR | None:
    raw = operation.get("requestBody")
    if raw is None:
        return None
    body = _resolve(document, raw, f"{pointer}/requestBody")
    if not isinstance(body, Mapping) or not isinstance(body.get("content"), Mapping):
        raise UnsupportedOpenAPIError(
            f"{pointer}/requestBody", "request body needs content", operation_id=operation_id
        )
    content = body["content"]
    if not content:
        raise UnsupportedOpenAPIError(
            f"{pointer}/requestBody/content",
            "at least one representation is required",
            operation_id=operation_id,
        )
    media_type = select_request_media(content)
    representation = content[media_type]
    schema = representation.get("schema", {}) if isinstance(representation, Mapping) else {}
    if _raw_media_type(media_type):
        expression = "bytes"
    else:
        expression = _schema_type(
            schema, f"{pointer}/requestBody/content/{media_type}", operation_id
        )
    required = bool(body.get("required", False))
    fields = _flat_body_fields(schema, media_type=media_type, body_required=required)
    resolved_schema = _resolve(
        document, schema, f"{pointer}/requestBody/content/{media_type}/schema"
    )
    wire_fields = _flat_body_fields(resolved_schema, media_type=media_type, body_required=required)
    _collect_model(expression, model_names)
    return RequestBodyIR(media_type, expression, required, fields, wire_fields)


def _body_projection(
    raw: Any,
    document: Mapping[str, Any],
    operation: Mapping[str, Any],
    body: RequestBodyIR | None,
    operation_pointer: str,
    operation_id: str,
    model_names: set[str],
) -> BodyProjectionIR | None:
    if raw is None:
        return None
    pointer = f"{operation_pointer}/x-eazy-sdk/projection"
    if not isinstance(raw, Mapping):
        raise UnsupportedOpenAPIError(
            pointer, "projection must be an object", operation_id=operation_id
        )
    unknown = set(raw) - {"source", "target", "application", "encoding", "name"}
    if unknown:
        raise UnsupportedOpenAPIError(
            pointer, f"unknown fields: {sorted(unknown)}", operation_id=operation_id
        )
    if body is None:
        raise UnsupportedOpenAPIError(
            pointer, "projection requires a request body", operation_id=operation_id
        )
    application = raw.get("application")
    if not isinstance(application, str) or not application:
        raise UnsupportedOpenAPIError(
            f"{pointer}/application",
            "application must be a non-empty named requirement",
            operation_id=operation_id,
        )
    encoding = raw.get("encoding")
    if not isinstance(encoding, str) or not encoding:
        raise UnsupportedOpenAPIError(
            f"{pointer}/encoding", "encoding must be a media type", operation_id=operation_id
        )
    supported = (
        encoding == "application/json"
        or encoding.endswith("+json")
        or encoding in {"application/x-www-form-urlencoded", "multipart/form-data"}
    )
    if not supported:
        raise UnsupportedOpenAPIError(
            f"{pointer}/encoding",
            "generated projections support JSON, form and multipart encodings",
            operation_id=operation_id,
        )
    if encoding != body.media_type:
        raise UnsupportedOpenAPIError(
            f"{pointer}/encoding",
            f"encoding must match selected request media type {body.media_type!r}",
            operation_id=operation_id,
        )
    name = raw.get("name")
    if name is not None and (not isinstance(name, str) or not name):
        raise UnsupportedOpenAPIError(
            f"{pointer}/name", "name must be a non-empty string", operation_id=operation_id
        )
    source_schema = raw.get("source")
    target_schema = raw.get("target")
    source_type = _schema_type(source_schema, f"{pointer}/source", operation_id)
    target_type = _schema_type(target_schema, f"{pointer}/target", operation_id)
    source_fields = _projection_object_fields(
        document, source_schema, f"{pointer}/source", operation_id
    )
    target_fields = _projection_object_fields(
        document, target_schema, f"{pointer}/target", operation_id
    )
    request_body = _resolve(
        document, operation.get("requestBody"), f"{operation_pointer}/requestBody"
    )
    content = request_body.get("content") if isinstance(request_body, Mapping) else None
    representation = content.get(body.media_type) if isinstance(content, Mapping) else None
    request_schema = representation.get("schema", {}) if isinstance(representation, Mapping) else {}
    resolved_request = _resolve(
        document,
        request_schema,
        f"{operation_pointer}/requestBody/content/{body.media_type}/schema",
    )
    resolved_target = _resolve(document, target_schema, f"{pointer}/target")
    if resolved_target != resolved_request:
        raise UnsupportedOpenAPIError(
            f"{pointer}/target",
            "target must identify the selected request-body wire schema",
            operation_id=operation_id,
        )
    _collect_model(source_type, model_names)
    _collect_model(target_type, model_names)
    for field in (*source_fields, *target_fields):
        _collect_model(field.type_expression, model_names)
    return BodyProjectionIR(
        source_type,
        target_type,
        source_fields,
        target_fields,
        application,
        encoding,
        name,
    )


def _projection_object_fields(
    document: Mapping[str, Any],
    schema: Any,
    pointer: str,
    operation_id: str,
) -> tuple[BodyFieldIR, ...]:
    resolved = _resolve(document, schema, pointer)
    if not isinstance(resolved, Mapping) or (
        resolved.get("type") != "object" and "properties" not in resolved
    ):
        raise UnsupportedOpenAPIError(
            pointer, "projection schema must be object-shaped", operation_id=operation_id
        )
    properties = resolved.get("properties")
    if not isinstance(properties, Mapping):
        raise UnsupportedOpenAPIError(
            f"{pointer}/properties",
            "projection schema requires object properties",
            operation_id=operation_id,
        )
    raw_required = resolved.get("required", ())
    if not isinstance(raw_required, Sequence) or isinstance(raw_required, str | bytes):
        raise UnsupportedOpenAPIError(
            f"{pointer}/required", "required must be an array", operation_id=operation_id
        )
    required = {str(item) for item in raw_required}
    unknown_required = required - {str(item) for item in properties}
    if unknown_required:
        raise UnsupportedOpenAPIError(
            f"{pointer}/required",
            f"unknown required properties: {sorted(unknown_required)}",
            operation_id=operation_id,
        )
    return tuple(
        BodyFieldIR(
            str(name),
            _schema_type(value, f"{pointer}/properties/{_escape_pointer(str(name))}", operation_id),
            str(name) in required,
        )
        for name, value in properties.items()
    )


def _flat_body_fields(
    schema: Any, *, media_type: str, body_required: bool
) -> tuple[BodyFieldIR, ...] | None:
    if not (
        media_type == "application/json"
        or media_type.endswith("+json")
        or media_type in {"application/x-www-form-urlencoded", "multipart/form-data"}
    ):
        return None
    if not isinstance(schema, Mapping) or "$ref" in schema:
        return None
    if any(key in schema for key in ("oneOf", "anyOf", "allOf", "discriminator")):
        return None
    if schema.get("type") != "object" and "properties" not in schema:
        return None
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return None
    raw_required = schema.get("required", ())
    if not isinstance(raw_required, Sequence) or isinstance(raw_required, str | bytes):
        return None
    required_names = {str(item) for item in raw_required}
    if not body_required and required_names:
        return None
    fields: list[BodyFieldIR] = []
    for raw_name, property_schema in properties.items():
        if not _is_flat_body_leaf(property_schema, media_type=media_type):
            return None
        name = str(raw_name)
        fields.append(
            BodyFieldIR(
                name,
                _schema_type(property_schema, "#/requestBody/properties", None),
                name in required_names,
            )
        )
    return tuple(fields)


def _is_flat_body_leaf(schema: Any, *, media_type: str) -> bool:
    if not isinstance(schema, Mapping) or "$ref" in schema:
        return False
    if any(key in schema for key in ("oneOf", "anyOf", "allOf", "discriminator")):
        return False
    schema_type = schema.get("type")
    if schema_type == "array":
        return _is_flat_body_leaf(schema.get("items"), media_type=media_type)
    if schema_type == "object" or "properties" in schema:
        return False
    allowed = {
        "type",
        "nullable",
        "enum",
        "description",
        "title",
        "default",
        "example",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
    schema_format = schema.get("format")
    if schema_format is not None:
        if media_type != "multipart/form-data" or schema_format not in {"binary", "byte"}:
            return False
        allowed.add("format")
    return not (set(schema) - allowed)


def _responses(
    document: Mapping[str, Any],
    operation: Mapping[str, Any],
    pointer: str,
    operation_id: str,
    model_names: set[str],
) -> tuple[ResponseIR, ...]:
    raw = operation.get("responses")
    if not isinstance(raw, Mapping) or not raw:
        raise UnsupportedOpenAPIError(
            f"{pointer}/responses", "at least one response is required", operation_id=operation_id
        )
    out: list[ResponseIR] = []
    for raw_status, raw_response in raw.items():
        status = _status_key(str(raw_status), f"{pointer}/responses/{raw_status}", operation_id)
        response = _resolve(document, raw_response, f"{pointer}/responses/{raw_status}")
        if not isinstance(response, Mapping):
            raise UnsupportedOpenAPIError(
                f"{pointer}/responses/{raw_status}",
                "response must be an object",
                operation_id=operation_id,
            )
        success = _is_success(status)
        content = response.get("content")
        if content is None:
            out.append(ResponseIR(status, None, None, success))
            continue
        if not isinstance(content, Mapping):
            raise UnsupportedOpenAPIError(
                f"{pointer}/responses/{raw_status}/content",
                "response content must be an object",
                operation_id=operation_id,
            )
        for media_type, representation in content.items():
            media_type = str(media_type)
            representation_mapping = representation if isinstance(representation, Mapping) else {}
            item_schema = representation_mapping.get("itemSchema")
            item_expression: str | None = None
            if item_schema is not None:
                if not version_supports_querystring(document):
                    raise UnsupportedOpenAPIError(
                        f"{pointer}/responses/{raw_status}/content/{media_type}/itemSchema",
                        "itemSchema requires OpenAPI 3.2",
                        operation_id=operation_id,
                    )
                item_expression = _schema_type(
                    item_schema,
                    f"{pointer}/responses/{raw_status}/content/{media_type}/itemSchema",
                    operation_id,
                )
                expression = f"list[{item_expression}]"
            elif _raw_media_type(media_type):
                expression = "bytes"
            elif media_type.startswith("text/"):
                expression = "str"
            else:
                schema = representation_mapping.get("schema", {})
                expression = _schema_type(
                    schema,
                    f"{pointer}/responses/{raw_status}/content/{media_type}",
                    operation_id,
                )
            _collect_model(expression, model_names)
            out.append(
                ResponseIR(
                    status,
                    media_type,
                    expression,
                    success,
                    item_type_expression=item_expression,
                )
            )
    return tuple(out)


def _security(
    raw: Any, pointer: str, operation_id: str
) -> tuple[SecurityRequirementIR, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise UnsupportedOpenAPIError(
            f"{pointer}/security", "security must be an array", operation_id=operation_id
        )
    alternatives: list[SecurityRequirementIR] = []
    for index, alternative in enumerate(raw):
        if not isinstance(alternative, Mapping):
            raise UnsupportedOpenAPIError(
                f"{pointer}/security/{index}",
                "security requirement must be an object",
                operation_id=operation_id,
            )
        schemes: list[tuple[str, tuple[str, ...]]] = []
        for scheme, scopes in alternative.items():
            if not isinstance(scopes, Sequence) or isinstance(scopes, str | bytes):
                raise UnsupportedOpenAPIError(
                    f"{pointer}/security/{index}/{scheme}",
                    "scopes must be an array",
                    operation_id=operation_id,
                )
            schemes.append((str(scheme), tuple(str(scope) for scope in scopes)))
        alternatives.append(SecurityRequirementIR(tuple(schemes)))
    return tuple(alternatives)


def _server(raw: Any, pointer: str) -> ServerIR | None:
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes) or not raw:
        raise UnsupportedOpenAPIError(f"{pointer}/servers", "servers must be a non-empty array")
    first = raw[0]
    if not isinstance(first, Mapping) or not isinstance(first.get("url"), str):
        raise UnsupportedOpenAPIError(f"{pointer}/servers/0", "server needs a URL")
    raw_variables = first.get("variables", {})
    if not isinstance(raw_variables, Mapping):
        raise UnsupportedOpenAPIError(
            f"{pointer}/servers/0/variables", "server variables must be an object"
        )
    variables: list[ServerVariableIR] = []
    for name, raw_variable in raw_variables.items():
        if not isinstance(raw_variable, Mapping) or not isinstance(
            raw_variable.get("default"), str
        ):
            raise UnsupportedOpenAPIError(
                f"{pointer}/servers/0/variables/{name}",
                "server variable needs a string default",
            )
        raw_enum = raw_variable.get("enum", ())
        if not isinstance(raw_enum, Sequence) or isinstance(raw_enum, str | bytes):
            raise UnsupportedOpenAPIError(
                f"{pointer}/servers/0/variables/{name}/enum",
                "server variable enum must be an array",
            )
        variables.append(
            ServerVariableIR(str(name), str(raw_variable["default"]), tuple(map(str, raw_enum)))
        )
    return ServerIR(str(first["url"]), tuple(variables))


def _schema_type(schema: Any, pointer: str, operation_id: str | None) -> str:
    if not isinstance(schema, Mapping):
        raise UnsupportedOpenAPIError(
            pointer, "schema must be an object", operation_id=operation_id
        )
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#/components/schemas/"):
            raise UnsupportedOpenAPIError(
                pointer,
                "schema $ref must target components/schemas",
                operation_id=operation_id,
            )
        return ref.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, str | bytes) and enum:
        expression = f"Literal[{', '.join(repr(item) for item in enum)}]"
        if schema.get("nullable") is True and "None" not in expression:
            expression += " | None"
        return expression
    if "oneOf" in schema or "anyOf" in schema:
        key = "oneOf" if "oneOf" in schema else "anyOf"
        variants = schema[key]
        if not isinstance(variants, Sequence):
            raise UnsupportedOpenAPIError(
                pointer, f"{key} must be an array", operation_id=operation_id
            )
        expression = " | ".join(_schema_type(item, pointer, operation_id) for item in variants)
    else:
        schema_type = schema.get("type")
        if isinstance(schema_type, Sequence) and not isinstance(schema_type, str | bytes):
            mapped = [_map_scalar(str(item)) for item in schema_type]
            return " | ".join(dict.fromkeys(mapped))
        if schema_type == "string" and schema.get("format") in {"binary", "byte"}:
            expression = "bytes"
        elif schema_type == "array":
            expression = f"list[{_schema_type(schema.get('items', {}), pointer, operation_id)}]"
        elif schema_type == "object" or "properties" in schema:
            expression = "dict[str, Any]"
        else:
            expression = _map_scalar(str(schema_type or "object"))
    if schema.get("nullable") is True and "None" not in expression:
        expression += " | None"
    return expression


def _map_scalar(value: str) -> str:
    return {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "null": "None",
        "object": "Any",
    }.get(value, "Any")


def _status_key(value: str, pointer: str, operation_id: str) -> int | str:
    if value == "default":
        return value
    if value.isdigit() and len(value) == 3:
        return int(value)
    normalized = value.upper()
    if len(normalized) == 3 and normalized[0] in "12345" and normalized[1:] == "XX":
        return normalized
    raise UnsupportedOpenAPIError(
        pointer,
        f"invalid response status key {value!r}",
        operation_id=operation_id,
    )


def _is_success(status: int | str) -> bool:
    return (isinstance(status, int) and 200 <= status < 300) or status == "2XX"


def select_request_media(content: Mapping[str, Any]) -> str:
    """Choose the one request representation supported by an endpoint contract.

    JSON is preferred because it retains the declared model. The compatibility
    report records every alternative that the generated method cannot select.
    """
    media_types = [str(media_type) for media_type in content]
    for media_type in media_types:
        if media_type == "application/json" or media_type.endswith("+json"):
            return media_type
    for preferred in (
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "application/octet-stream",
    ):
        if preferred in media_types:
            return preferred
    return media_types[0]


def _raw_media_type(media_type: str) -> bool:
    return not (
        media_type == "application/json"
        or media_type.endswith("+json")
        or media_type.startswith("text/")
        or media_type in {"application/jsonl", "application/x-ndjson", "application/json-seq"}
        or media_type.endswith("+json-seq")
    )


def _collect_model(expression: str, output: set[str]) -> None:
    normalized = expression.replace("[", " ").replace("]", " ").replace("|", " ").replace(",", " ")
    for token in normalized.split():
        if token not in {
            "str",
            "int",
            "float",
            "bool",
            "bytes",
            "None",
            "Any",
            "Literal",
            "list",
            "dict",
        } and not token.startswith(("'", '"')):
            output.add(token)


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def version_supports_querystring(document: Mapping[str, Any]) -> bool:
    """Return whether this document uses OpenAPI 3.2 or newer."""
    return str(document.get("openapi", "")).startswith("3.2.")
