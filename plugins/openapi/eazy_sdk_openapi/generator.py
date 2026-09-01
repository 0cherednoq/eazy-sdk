"""Deterministic emission of thin SDK facades for the shared Eazy SDK runtime."""

from __future__ import annotations

import json
import keyword
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compatibility import analyze_openapi
from .ir import (
    DependencyIR,
    OpenAPIIR,
    OperationIR,
    ParameterIR,
    ResponseIR,
    SecuritySchemeIR,
    SessionAuthIR,
    SessionCallIR,
    parse_openapi,
)

_GENERATED_RESERVED_NAMES = frozenset(
    {
        "Any",
        "ApiError",
        "ApiKeyScheme",
        "AsyncAPI",
        "BasicScheme",
        "BearerScheme",
        "Bytes",
        "BytesBody",
        "CallOptions",
        "ClientConfig",
        "CryptoRegistry",
        "CryptoRule",
        "Cookie",
        "CookieScheme",
        "DEFAULT",
        "DependencySpec",
        "Empty",
        "Error",
        "FormBody",
        "Header",
        "Json",
        "JsonBody",
        "MultipartBody",
        "Path",
        "Providers",
        "Query",
        "QueryString",
        "ResponseEnvelope",
        "Responses",
        "RetryPolicy",
        "StatusRange",
        "Success",
        "SyncAPI",
        "Text",
        "UNSET",
        "Unset",
        "AsyncClient",
        "Client",
        "WireOptions",
        "PayloadCrypto",
    }
)

_REQUEST_RESERVED_NAMES = frozenset({"self", "options", "request"})


@dataclass(frozen=True, slots=True)
class ProjectionImport:
    requirement: str
    implementation: str

    def __post_init__(self) -> None:
        if not self.requirement:
            raise ValueError("projection requirement must be non-empty")
        module, separator, attribute = self.implementation.partition(":")
        if (
            not separator
            or not attribute.isidentifier()
            or keyword.iskeyword(attribute)
            or not module
            or any(
                not part.isidentifier() or keyword.iskeyword(part)
                for part in module.split(".")
            )
        ):
            raise ValueError(
                "projection implementation must use a static 'module:attribute' import"
            )


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    projections: tuple[ProjectionImport, ...] = ()


def render_client(ir: OpenAPIIR, *, config: GenerationConfig | None = None) -> str:
    has_crypto = any(operation.crypto is not None for operation in ir.operations)
    projection_imports = _projection_imports(ir, config or GenerationConfig())
    has_projection = any(operation.body_projection is not None for operation in ir.operations)
    model_bindings = _model_bindings(ir.model_names, reserved=_generated_type_reserved(ir))
    models = _render_model_imports(model_bindings)
    routers = _router_groups(ir.operations)
    dependency_names = [
        name
        for dependency in ir.dependencies
        for name in (_constant(dependency.name), f"{_constant(dependency.name)}_SPEC")
    ]
    dependency_import = (
        f"from .dependencies import {', '.join(dependency_names)}" if dependency_names else ""
    )
    lines = [
        '"""Generated declarative sync/async API methods. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    if has_crypto:
        lines.extend(["from collections.abc import Mapping", ""])
    if ir.session_auth is not None or ir.protection_flows:
        lines.extend(["from dataclasses import replace", ""])
    lines.extend(
        [
            (
                "from typing import Annotated, Any, Callable, Literal, Required, "
                "TypedDict, Unpack, cast"
                if has_projection
                else "from typing import Annotated, Any, Literal, Required, TypedDict, Unpack, cast"
            ),
            "",
            "from pydantic import ConfigDict, Field",
            "from zapros import AsyncBaseHandler, BaseHandler",
            "",
            "from eazy_sdk import AsyncSdk, HandlerProfile, SyncSdk, api, api_group",
            "from eazy_sdk.codegen import (",
            "    DEFAULT, ApiError, AsyncApi, AsyncClient, Bytes, BytesBody, CallOptions,",
            "    Client, ClientConfig,",
            "    Cookie, DependencySpec, Empty, Form, FormBody, Header, JsonBody, JsonField,",
            "    MultipartBody, Part, Path, Query, QueryString, SyncApi,",
        ]
    )
    if any(operation.protections or operation.body_projection for operation in ir.operations):
        lines.append("    BodyProjection,")
    if ir.session_auth is not None:
        lines.append("    AuthContext, _generated_session_auth, _generated_session_scheme,")
    lines.extend(
        [
            "    Error, Json, ResponseEnvelope, Responses, StatusRange, Success, Text,",
            "    WireOptions, all_of, any_of,",
            "    FromProtection, ProtectionRequirement, protection_flow,",
            ")",
        ]
    )
    if has_crypto:
        lines.extend(
            [
                "from eazy_sdk.crypto import (",
                "    CryptoDirection, CryptoRegistry, CryptoRule, PayloadCrypto,",
                "    http_crypto_scope, http_encrypted,",
                ")",
            ]
        )
    lines.extend(
        [
            "from .auth import *",
            dependency_import,
            "from ._model_base import OpenAPIModel",
            "from .signatures import *",
            models,
            "",
        ]
    )
    for requirement, implementation in projection_imports.items():
        module, attribute = implementation.split(":", 1)
        lines.append(
            f"from {module} import {attribute} as {_projection_import_name(requirement)}"
        )
    if projection_imports:
        lines.append("")
    if ir.session_auth is not None:
        session = ir.session_auth
        lines.extend(
            [
                f"{_constant(session.scheme)} = _generated_session_scheme(",
                f"    {session.session_model},",
                f"    name={session.scheme!r},",
                f"    bearer_field={session.bearer_field!r},",
                f"    refresh_field={session.refresh_token_field!r},",
                f"    expires_field={session.expires_at_field!r},",
                f"    expires_leeway_seconds={session.expires_leeway_seconds!r},",
                ")",
                "",
            ]
        )
    if has_crypto:
        lines.extend(_crypto_registry(ir))
    for flow in ir.protection_flows:
        lines.extend(
            [
                f"{_protection_constant(flow.name)} = ProtectionRequirement["
                f"{_type(flow.result_type, model_bindings)}]({flow.name!r})",
                "",
            ]
        )
    for operation in ir.operations:
        lines.extend(_operation_body_model(operation, model_bindings))
    for operation in ir.operations:
        lines.extend(_operation_request_model(operation, model_bindings))
    for operation in ir.operations:
        lines.extend(_operation_errors(operation, model_bindings))
    for _attribute, class_name, operations in routers:
        lines.extend(
            _router_class(
                f"Async{class_name}",
                "AsyncApi",
                operations,
                model_bindings,
                asynchronous=True,
            )
        )
    if ir.session_auth is not None:
        lines.extend(_session_service(ir.session_auth, routers, model_bindings))
    if ir.protection_flows:
        lines.extend(_protection_config(ir, routers))
    lines.extend(
        _api_facade(
            "AsyncAPI",
            "AsyncClient",
            "Async",
            routers,
            asynchronous=True,
            session_auth=ir.session_auth,
            has_protections=bool(ir.protection_flows),
        )
    )
    for _attribute, class_name, operations in routers:
        lines.extend(
            _router_class(
                f"Sync{class_name}",
                "SyncApi",
                operations,
                model_bindings,
                asynchronous=False,
            )
        )
    lines.extend(
        _api_facade(
            "SyncAPI",
            "Client",
            "Sync",
            routers,
            asynchronous=False,
            session_auth=ir.session_auth,
            has_protections=bool(ir.protection_flows),
        )
    )
    exports = [
        "AsyncAPI",
        *(f"Async{class_name}" for _attribute, class_name, _operations in routers),
        "SyncAPI",
        *(f"Sync{class_name}" for _attribute, class_name, _operations in routers),
    ]
    if has_crypto:
        exports.append("crypto_registry")
    lines.extend(["", "", f"__all__ = {exports!r}", ""])
    return "\n".join(lines)


def _crypto_registry(ir: OpenAPIIR) -> list[str]:
    operations = tuple(operation for operation in ir.operations if operation.crypto is not None)
    required = tuple(
        dict.fromkeys(
            operation.crypto.profile for operation in operations if operation.crypto
        )
    )
    rules: list[str] = []
    for operation in operations:
        crypto = operation.crypto
        assert crypto is not None
        directions = {
            "outbound": ("CryptoDirection.OUTBOUND",),
            "inbound": ("CryptoDirection.INBOUND",),
            "bidirectional": (
                "CryptoDirection.OUTBOUND",
                "CryptoDirection.INBOUND",
            ),
        }[crypto.direction]
        trailing_comma = "," if len(directions) == 1 else ""
        direction_expression = (
            f"frozenset(({', '.join(directions)}{trailing_comma}))"
        )
        wire = crypto.wire
        rules.extend(
            [
                "        CryptoRule(",
                f"            profile=profiles[{crypto.profile!r}],",
                "            scope=http_crypto_scope(",
                f"                operation_ids=({operation.operation_id!r},),",
                "            ),",
                "            wire=http_encrypted(",
                f"                content_type={wire.content_type!r},",
                f"                clear_content_type={wire.clear_content_type!r},",
                f"                plaintext_statuses=frozenset({wire.plaintext_statuses!r}),",
                "            ),",
                f"            directions={direction_expression},",
                "        ),",
            ]
        )
    return [
        "",
        "",
        "def crypto_registry(profiles: Mapping[str, PayloadCrypto]) -> CryptoRegistry:",
        f"    required = frozenset({required!r})",
        "    missing = required.difference(profiles)",
        "    if missing:",
        "        raise KeyError(f'missing crypto profiles: {sorted(missing)!r}')",
        "    return CryptoRegistry(",
        "        (",
        *rules,
        "        )",
        "    )",
    ]


def _router_groups(
    operations: tuple[OperationIR, ...],
) -> list[tuple[str, str, tuple[OperationIR, ...]]]:
    grouped: dict[str, list[OperationIR]] = {}
    for operation in operations:
        tag = operation.tags[0] if operation.tags else "default"
        grouped.setdefault(tag, []).append(operation)
    routers: list[tuple[str, str, tuple[OperationIR, ...]]] = []
    used_attributes: dict[str, str] = {}
    used_classes: dict[str, str] = {}
    for tag, tagged_operations in grouped.items():
        attribute = _router_attribute(tag)
        class_name = _pascal(tag) or "Default"
        if attribute in used_attributes and used_attributes[attribute] != tag:
            raise ValueError(f"router tags {used_attributes[attribute]!r} and {tag!r} collide")
        if class_name in used_classes and used_classes[class_name] != tag:
            raise ValueError(f"router tags {used_classes[class_name]!r} and {tag!r} collide")
        used_attributes[attribute] = tag
        used_classes[class_name] = tag
        routers.append((attribute, class_name, tuple(tagged_operations)))
    return routers


def _router_class(
    class_name: str,
    api_type: str,
    operations: tuple[OperationIR, ...],
    model_bindings: Mapping[str, str],
    *,
    asynchronous: bool,
) -> list[str]:
    lines = [
        "",
        "",
        f"class {class_name}({api_type}):",
    ]
    for operation in operations:
        lines.extend(_method(operation, model_bindings, asynchronous=asynchronous))
    return lines


def _api_facade(
    class_name: str,
    client_type: str,
    router_prefix: str,
    routers: list[tuple[str, str, tuple[OperationIR, ...]]],
    *,
    asynchronous: bool,
    session_auth: SessionAuthIR | None,
    has_protections: bool,
) -> list[str]:
    sdk_base = "AsyncSdk" if asynchronous else "SyncSdk"
    lines = [
        "",
        "",
        f"class {class_name}({sdk_base}):",
    ]
    for attribute, router_name, _operations in routers:
        bound_class = f"{router_prefix}{router_name}"
        lines.append(f"    {attribute} = api_group({bound_class})")
    if not asynchronous and session_auth is not None:
        lines.extend(
            [
                "",
                (
                    f"    def __init__(self, client: {client_type}, *, "
                    "owns_client: bool = False) -> None:"
                ),
                "        super().__init__(client, owns_client=owns_client)",
                "        auth_client = cast(AsyncClient, client._async_view())",
                "        self._auth_api = auth_client.bind_sdk(AsyncAPI)",
            ]
        )
    handler_base = "AsyncBaseHandler" if asynchronous else "BaseHandler"
    lines.extend(["", "    @classmethod", "    def from_handler("])
    if session_auth is None:
        lines.extend(
            [
                "        cls,",
                "        *,",
                "        base_url: str = '',",
                f"        handler: {handler_base},",
                "        config: ClientConfig | None = None,",
                "        owns_handler: bool = True,",
                "        profile: HandlerProfile | None = None,",
            ]
        )
    else:
        lines.extend(
            [
                "        cls,",
                "        *,",
                "        base_url: str = '',",
                f"        handler: {handler_base},",
                f"        credentials: {session_auth.credentials_model} | None = None,",
                f"        session: {session_auth.session_model} | None = None,",
                "        config: ClientConfig | None = None,",
                "        owns_handler: bool = True,",
                "        profile: HandlerProfile | None = None,",
            ]
        )
    lines.extend(
        [
            f"    ) -> {class_name}:",
        ]
    )
    if has_protections:
        lines.append("        config = _protection_config(config)")
    if session_auth is not None:
        lines.append(
            "        config = _session_config(config, credentials=credentials, session=session)"
        )
    lines.extend(
        [
            "        return super().from_handler(",
            "            base_url=base_url,",
            "            handler=handler,",
            "            config=config,",
            "            owns_handler=owns_handler,",
            "            profile=profile,",
            "        )",
        ]
    )
    raw_client = "httpx.AsyncClient" if asynchronous else "httpx.Client"
    handler_type = "AsyncHttpxHandler" if asynchronous else "HttpxHandler"
    lines.extend(["", "    @classmethod", "    def httpx("])
    if session_auth is None:
        lines.append("        cls, *, base_url: str, config: ClientConfig | None = None")
    else:
        lines.extend(
            [
                "        cls,",
                "        *,",
                "        base_url: str,",
                f"        credentials: {session_auth.credentials_model} | None = None,",
                f"        session: {session_auth.session_model} | None = None,",
                "        config: ClientConfig | None = None,",
            ]
        )
    lines.extend(
        [
            f"    ) -> {class_name}:",
            "        import httpx",
            f"        from eazy_sdk.handlers.httpx import {handler_type}",
            "",
            f"        raw = {raw_client}(base_url=base_url, headers={{}}, cookies={{}})",
            f"        handler = {handler_type}(raw, owns_client=True)",
            "        return cls.from_handler(",
            "            base_url=base_url,",
            "            handler=handler,",
        ]
    )
    if session_auth is not None:
        lines.extend(
            [
                "            credentials=credentials,",
                "            session=session,",
            ]
        )
    lines.extend(["            config=config,", "        )"])
    return lines


def _session_service(
    session: SessionAuthIR,
    routers: list[tuple[str, str, tuple[OperationIR, ...]]],
    model_bindings: Mapping[str, str],
) -> list[str]:
    lines = [
        "",
        "",
        "def _session_value(value: object) -> Any:",
        "    reveal = getattr(value, 'get_secret_value', None)",
        "    return reveal() if callable(reveal) else value",
        "",
        "",
        "class _GeneratedSessionService:",
    ]
    lines.extend(
        _session_service_method(
            "acquire",
            session.acquire,
            session.credentials_model,
            "credentials",
            session.session_model,
            routers,
            model_bindings,
        )
    )
    if session.refresh is not None:
        lines.extend(
            _session_service_method(
                "refresh",
                session.refresh,
                session.session_model,
                "session",
                session.session_model,
                routers,
                model_bindings,
            )
        )
    lines.extend(
        [
            "",
            "",
            "def _session_config(",
            "    config: ClientConfig | None,",
            "    *,",
            f"    credentials: {session.credentials_model} | None,",
            f"    session: {session.session_model} | None,",
            ") -> ClientConfig:",
            "    if (credentials is None) == (session is None):",
            "        raise ValueError('provide exactly one of credentials or session')",
            "    base = config or ClientConfig()",
            "    if base.auth is not None:",
            "        raise ValueError(",
            "            'config.auth cannot be combined with credentials or session'",
            "        )",
            "    auth = _generated_session_auth(",
            f"        {session.session_model},",
            f"        bearer_field={session.bearer_field!r},",
            f"        refresh_field={session.refresh_token_field!r},",
            f"        expires_field={session.expires_at_field!r},",
            f"        expires_leeway_seconds={session.expires_leeway_seconds!r},",
            "        credentials=credentials,",
            "        session=session,",
            "        service=_GeneratedSessionService(),",
            f"        scheme={_constant(session.scheme)},",
            "    )",
            "    return replace(base, auth=auth)",
        ]
    )
    return lines


def _protection_config(
    ir: OpenAPIIR,
    routers: list[tuple[str, str, tuple[OperationIR, ...]]],
) -> list[str]:
    flows: list[str] = []
    for flow in ir.protection_flows:
        acquire = _async_operation_reference(flow.acquire, routers)
        verify = (
            _async_operation_reference(flow.verify, routers) if flow.verify is not None else None
        )
        arguments = [
            _protection_constant(flow.name),
            f"acquire={acquire}",
        ]
        if flow.solve:
            arguments.append("solve=True")
        if verify is not None:
            arguments.append(f"verify={verify}")
        flows.append(f"protection_flow({', '.join(arguments)})")
    return [
        "",
        "",
        "def _protection_config(config: ClientConfig | None) -> ClientConfig:",
        "    base = config or ClientConfig()",
        f"    generated = {_tuple_expression(flows)}",
        "    return replace(",
        "        base,",
        "        operation_protections=(*generated, *base.operation_protections),",
        "    )",
    ]


def _async_operation_reference(
    operation_id: str,
    routers: list[tuple[str, str, tuple[OperationIR, ...]]],
) -> str:
    class_name = next(
        class_name
        for _attribute, class_name, operations in routers
        if any(item.operation_id == operation_id for item in operations)
    )
    return f"Async{class_name}.{_identifier(operation_id)}"


def _session_service_method(
    name: str,
    call: SessionCallIR,
    argument_type: str,
    argument_name: str,
    session_type: str,
    routers: list[tuple[str, str, tuple[OperationIR, ...]]],
    model_bindings: Mapping[str, str],
) -> list[str]:
    router_attribute = next(
        attribute
        for attribute, _class_name, operations in routers
        if any(item.operation_id == call.operation for item in operations)
    )
    values: list[str] = []
    for value in call.values:
        if value.is_literal:
            expression = repr(value.literal)
        else:
            assert value.source is not None
            expected_prefix = f"{argument_name}."
            if not value.source.startswith(expected_prefix):
                raise ValueError(
                    f"session {name} mapping {value.source!r} must start with {expected_prefix!r}"
                )
            path = value.source.removeprefix(expected_prefix)
            if not path or not all(part.isidentifier() for part in path.split(".")):
                raise ValueError(f"invalid session attribute path {value.source!r}")
            expression = f"_session_value({argument_name}.{path})"
        values.append(f"            {value.target}={expression},")
    method = _identifier(call.operation)
    return [
        "",
        f"    async def {name}(",
        "        self,",
        f"        {argument_name}: {argument_type},",
        "        context: AuthContext,",
        f"    ) -> {session_type}:",
        f"        request = {_type(call.request_model, model_bindings)}(",
        *values,
        "        )",
        f"        return cast({session_type}, await context.sdk.{router_attribute}.{method}(",
        f"            {call.request_field}=request",
        "        ))",
    ]


def _router_attribute(tag: str) -> str:
    separated = re.sub(r"(?<!^)(?=[A-Z])", "_", tag)
    result = re.sub(r"\W+", "_", separated).strip("_").lower() or "default"
    if result[0].isdigit():
        result = f"_{result}"
    return f"{result}_" if keyword.iskeyword(result) else result


def render_auth(ir: OpenAPIIR) -> str:
    lines = [
        '"""Generated security scheme identities. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        "from eazy_sdk.codegen import ApiKeyScheme, BasicScheme, BearerScheme, CookieScheme",
        "",
    ]
    managed = ir.session_auth.scheme if ir.session_auth is not None else None
    for scheme in ir.security_schemes:
        if scheme.name == managed:
            continue
        lines.append(f"{_constant(scheme.name)} = {_security_scheme(scheme)}")
    exports = ", ".join(
        repr(_constant(item.name)) for item in ir.security_schemes if item.name != managed
    )
    lines.extend(["", f"__all__ = [{exports}]", ""])
    return "\n".join(lines)


def render_dependencies(ir: OpenAPIIR) -> str:
    model_bindings = _model_bindings(ir.model_names)
    models = _render_model_imports(model_bindings)
    lines = [
        '"""Generated dependency identities and typed provider facade. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Literal",
        "",
        "from eazy_sdk.codegen import (",
        "    DependencyCachePolicy, DependencyProvider, DependencyRegistry, DependencySpec,",
        "    RequestDependency, field, value,",
        ")",
        models,
        "",
    ]
    for dependency in ir.dependencies:
        lines.extend(_dependency(dependency, model_bindings))
    lines.extend(
        [
            "class Providers:",
            "    def __init__(self) -> None:",
            "        self.registry = DependencyRegistry()",
        ]
    )
    if not ir.dependencies:
        lines.append("        pass")
    for dependency in ir.dependencies:
        name = _identifier(dependency.name)
        constant = _constant(dependency.name)
        result_type = _type(dependency.result_type, model_bindings)
        lines.extend(
            [
                "",
                f"    def {name}(self, provider: DependencyProvider[{result_type}]) -> Providers:",
                f"        self.registry.register({constant}, provider)",
                "        return self",
            ]
        )
    exports = [*(_constant(item.name) for item in ir.dependencies), "Providers"]
    lines.extend(["", "", f"__all__ = {exports!r}", ""])
    return "\n".join(lines)


def render_signatures(ir: OpenAPIIR) -> str:
    lines = [
        '"""Generated imports for named signature descriptors. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    exports: list[str] = []
    for signature in ir.signature_profiles:
        module, attribute = signature.implementation.split(":", 1)
        name = _constant(signature.name)
        lines.append(f"from {module} import {attribute} as {name}")
        exports.append(name)
    lines.extend(["", f"__all__ = {exports!r}", ""])
    return "\n".join(lines)


def _projection_imports(
    ir: OpenAPIIR, config: GenerationConfig
) -> dict[str, str]:
    configured: dict[str, str] = {}
    for item in config.projections:
        if item.requirement in configured:
            raise ValueError(f"duplicate projection requirement {item.requirement!r}")
        configured[item.requirement] = item.implementation
    required = {
        operation.body_projection.application
        for operation in ir.operations
        if operation.body_projection is not None
    }
    missing = sorted(required - configured.keys())
    if missing:
        raise ValueError(f"missing projection imports for requirements: {missing}")
    aliases: dict[str, str] = {}
    for requirement in required:
        alias = _projection_import_name(requirement)
        previous = aliases.get(alias)
        if previous is not None and previous != requirement:
            raise ValueError(
                f"projection requirements {previous!r} and {requirement!r} "
                f"normalize to the same generated import {alias!r}"
            )
        aliases[alias] = requirement
    return {name: configured[name] for name in sorted(required)}


def _projection_import_name(requirement: str) -> str:
    return f"_{_constant(requirement)}_PROJECTION_APPLICATION"


def _dependency(item: DependencyIR, model_bindings: Mapping[str, str]) -> list[str]:
    constant = _constant(item.name)
    cache = item.cache.upper()
    bindings: list[str] = []
    for binding in item.bindings:
        selector = f"field({binding.field!r})" if binding.field is not None else "value()"
        target = "body" if binding.location == "body" else binding.location
        bindings.append(f"{selector}.to_{target}({binding.name!r})")
    expression = _tuple_expression(bindings)
    return [
        f"{constant} = RequestDependency.typed(",
        f"    {item.name!r}, {_type(item.result_type, model_bindings)}, "
        f"cache=DependencyCachePolicy.{cache}, secret={item.secret!r},",
        ")",
        f"{constant}_SPEC = DependencySpec({constant}, None, {expression})",
        "",
    ]


def generate_package(
    document: Mapping[str, Any],
    *,
    spec_path: Path,
    output_directory: Path,
    package_name: str,
    config: GenerationConfig | None = None,
) -> Path:
    if not package_name.isidentifier() or keyword.iskeyword(package_name):
        raise ValueError(f"invalid Python package name: {package_name!r}")
    compatibility = analyze_openapi(document)
    ir = parse_openapi(document)
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_directory) as temporary:
        staged = Path(temporary) / package_name
        staged.mkdir()
        _write_text(staged / "_model_base.py", render_model_base())
        _generate_models(spec_path, staged / "models.py", package_name=package_name)
        _write_text(staged / "auth.py", render_auth(ir))
        _write_text(staged / "dependencies.py", render_dependencies(ir))
        _write_text(staged / "signatures.py", render_signatures(ir))
        _write_text(staged / "client.py", render_client(ir, config=config))
        _write_text(staged / "py.typed", "")
        _write_text(
            staged / "openapi-compatibility.json",
            json.dumps(compatibility.as_dict(), indent=2, ensure_ascii=False) + "\n",
        )
        _write_text(
            staged / "__init__.py",
            '"""Generated Eazy SDK SDK."""\n\n'
            + (
                "from .client import AsyncAPI, SyncAPI, crypto_registry\n\n"
                '__all__ = ["AsyncAPI", "SyncAPI", "crypto_registry"]\n'
                if any(operation.crypto is not None for operation in ir.operations)
                else "from .client import AsyncAPI, SyncAPI\n\n"
                '__all__ = ["AsyncAPI", "SyncAPI"]\n'
            ),
        )
        destination = output_directory / package_name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(staged), destination)
    return destination


def render_model_base() -> str:
    return '''"""Generated OpenAPI model serialization policy. Do not edit."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class OpenAPIModel(BaseModel):
    """Preserve omitted fields while retaining explicitly supplied nulls."""

    model_config = ConfigDict(serialize_by_alias=True)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(**kwargs)


__all__ = ["OpenAPIModel"]
'''


def _generate_models(spec_path: Path, output: Path, *, package_name: str) -> None:
    command = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(spec_path),
        "--input-file-type",
        "openapi",
        "--output",
        str(output),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--base-class",
        f"{package_name}._model_base.OpenAPIModel",
        "--use-standard-collections",
        "--use-union-operator",
        "--use-annotated",
        "--disable-timestamp",
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Pydantic model generation failed") from exc
    if not output.exists():
        _write_text(output, '"""No generated models."""\n')
    else:
        _write_text(output, output.read_text(encoding="utf-8"))


def _write_text(path: Path, content: str) -> None:
    path.write_text(content.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def _parameter_marker(item: ParameterIR, python_name: str) -> str:
    descriptor = {
        "path": "Path",
        "query": "Query",
        "querystring": "QueryString",
        "header": "Header",
        "cookie": "Cookie",
    }[item.location]
    arguments = [repr(item.name)]
    if item.location == "querystring":
        arguments.append(f"content_type={item.content_type!r}")
    else:
        defaults = {
            "path": ("simple", False),
            "query": ("form", True),
            "header": ("simple", False),
            "cookie": ("form", True),
        }
        default_style, default_explode = defaults[item.location]
        if item.style != default_style:
            arguments.append(f"style={item.style!r}")
        if item.explode != default_explode:
            arguments.append(f"explode={item.explode!r}")
        if item.allow_reserved:
            arguments.append("allow_reserved=True")
    return f"{descriptor}({', '.join(arguments)})"


def _root_body_marker(media_type: str) -> str:
    if media_type == "application/x-www-form-urlencoded":
        descriptor = "FormBody"
    elif media_type == "multipart/form-data":
        descriptor = "MultipartBody"
    elif media_type == "application/json" or media_type.endswith("+json"):
        descriptor = "JsonBody"
    elif media_type == "application/octet-stream":
        descriptor = "BytesBody"
    else:
        descriptor = "BytesBody"
    return f"{descriptor}(content_type={media_type!r})"


def _body_field_marker(media_type: str, wire_name: str) -> str:
    if media_type == "application/x-www-form-urlencoded":
        descriptor = "Form"
    elif media_type == "multipart/form-data":
        descriptor = "Part"
    else:
        descriptor = "JsonField"
    return f"{descriptor}({wire_name!r})"


def _generated_type_reserved(ir: OpenAPIIR) -> set[str]:
    names = set(
        _error_name(operation, response.status)
        for operation in ir.operations
        for response in operation.responses
        if not response.success
    )
    names.update(_request_model_name(operation) for operation in ir.operations)
    names.update(
        _projection_source_model_name(operation)
        for operation in ir.operations
        if operation.body_projection is not None
    )
    return names


def _request_field_names(operation: OperationIR) -> list[str]:
    identities = [(item.location, item.name) for item in operation.parameters]
    if operation.request_body is not None:
        if operation.protections or operation.body_projection is not None:
            identities.extend(
                ("body", item.name) for item in _projection_source_fields(operation)
            )
        else:
            identities.append(("body", "body"))
    return _normalize_request_identities(identities)


def _normalize_request_identities(identities: list[tuple[str, str]]) -> list[str]:
    used = set(_REQUEST_RESERVED_NAMES)
    names: list[str] = []
    for location, wire_name in identities:
        base = _request_identifier(wire_name)
        candidate = base
        if candidate in used:
            candidate = f"{base}_{location}"
        suffix = 2
        while candidate in used or keyword.iskeyword(candidate):
            candidate = f"{base}_{location}_{suffix}"
            suffix += 1
        used.add(candidate)
        names.append(candidate)
    return names


def _request_identifier(value: str) -> str:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    result = "".join(
        character if ("_" + character).isidentifier() else "_" for character in separated
    )
    result = re.sub(r"_+", "_", result).strip("_").lower()
    if not result:
        result = "field"
    if not (result[0] + "_").isidentifier():
        result = f"_{result}"
    if keyword.iskeyword(result) or result in _REQUEST_RESERVED_NAMES:
        result += "_"
    return result


def _operation_errors(operation: OperationIR, model_bindings: Mapping[str, str]) -> list[str]:
    errors = _errors(operation)
    lines: list[str] = []
    for name, model in errors.items():
        lines.extend(
            [f"class {name}(ApiError[{_type(model, model_bindings)}]):", "    pass", "", ""]
        )
    return lines


def _operation_request_model(
    operation: OperationIR,
    model_bindings: Mapping[str, str],
) -> list[str]:
    names = _request_field_names(operation)
    if not names:
        return []
    base = (
        _projection_source_model_name(operation)
        if operation.protections or operation.body_projection is not None
        else "TypedDict"
    )
    lines = [f"class {_request_model_name(operation)}({base}, total=False):"]
    parameter_names = names[: len(operation.parameters)]
    for item, python_name in zip(operation.parameters, parameter_names, strict=False):
        annotation = _type(item.type_expression, model_bindings)
        if not item.required and "None" not in annotation:
            annotation = f"{annotation} | None"
        annotation = f"Annotated[{annotation}, {_parameter_marker(item, python_name)}]"
        if item.required:
            annotation = f"Required[{annotation}]"
        lines.append(f"    {python_name}: {annotation}")
    if (
        operation.request_body is not None
        and not operation.protections
        and operation.body_projection is None
    ):
        body = operation.request_body
        if body.fields is not None or operation.protections:
            annotation = _body_model_name(operation)
        else:
            annotation = _type(body.type_expression, model_bindings)
        if not body.required and "None" not in annotation:
            annotation = f"{annotation} | None"
        annotation = f"Annotated[{annotation}, {_root_body_marker(body.media_type)}]"
        if body.required:
            annotation = f"Required[{annotation}]"
        lines.append(f"    {names[-1]}: {annotation}")
    if len(lines) == 1:
        lines.append("    pass")
    lines.extend(["", ""])
    return lines


def _request_model_name(operation: OperationIR) -> str:
    return f"{_pascal(operation.operation_id)}Request"


def _operation_body_model(operation: OperationIR, model_bindings: Mapping[str, str]) -> list[str]:
    body = operation.request_body
    fields = None if body is None else body.fields
    projected = bool(operation.protections or operation.body_projection is not None)
    if operation.body_projection is not None:
        fields = operation.body_projection.target_fields
    elif body is not None and operation.protections:
        fields = body.wire_fields
    if body is None or fields is None:
        return []
    managed = {
        output.target: (use.flow, output.source)
        for use in operation.protections
        for output in use.outputs
    }
    lines: list[str] = []
    if operation.body_projection is None:
        lines.extend(
            [
                f"class {_body_model_name(operation)}(OpenAPIModel):",
                "    model_config = ConfigDict(",
                "        extra='forbid', populate_by_name=True, serialize_by_alias=True",
                "    )",
            ]
        )
        for field in fields:
            if field.name not in managed:
                lines.append(_body_model_field(field, model_bindings))
        if len(lines) == 4:
            lines.append("    pass")
        lines.extend(["", ""])
    if projected:
        lines.extend(
            [
                f"class {_projection_target_model_name(operation)}(OpenAPIModel):",
                "    model_config = ConfigDict(",
                "        extra='forbid', populate_by_name=True, serialize_by_alias=True",
                "    )",
            ]
        )
        for field in fields:
            lines.append(
                _body_model_field(field, model_bindings, protection=managed.get(field.name))
            )
        lines.extend(["", ""])
        source_fields = _projection_source_fields(operation)
        source_names = _request_field_names(operation)[len(operation.parameters) :]
        lines.append(
            f"class {_projection_source_model_name(operation)}(TypedDict, total=False):"
        )
        for field, python_name in zip(source_fields, source_names, strict=True):
            annotation = _type(field.type_expression, model_bindings)
            required = (
                field.required
                if operation.body_projection is not None
                else bool(body.required and field.required)
            )
            if not required and "None" not in annotation:
                annotation = f"{annotation} | None"
            if required:
                annotation = f"Required[{annotation}]"
            lines.append(f"    {python_name}: {annotation}")
        if not source_fields:
            lines.append("    pass")
        lines.extend(["", ""])
        if operation.body_projection is None:
            mapper_name = _projection_mapper_name(operation)
            lines.extend(
                [
                    f"def {mapper_name}(",
                    f"    source: {_projection_source_model_name(operation)},",
                    f") -> {_projection_target_model_name(operation)}:",
                    "    document: dict[str, object] = {}",
                ]
            )
            for field, python_name in zip(source_fields, source_names, strict=True):
                target_name = _request_identifier(field.name)
                lines.extend(
                    [
                        f"    if {python_name!r} in source:",
                        f"        document[{target_name!r}] = source[{python_name!r}]",
                    ]
                )
            lines.extend(
                [
                    f"    return cast({_projection_target_model_name(operation)}, document)",
                    "",
                    "",
                ]
            )
            using = mapper_name
            projection_name = "openapi:" + operation.operation_id
        else:
            requirement = operation.body_projection.application
            using = (
                f"cast(Callable[[{_projection_source_model_name(operation)}], "
                f"{_projection_target_model_name(operation)}], "
                f"{_projection_import_name(requirement)})"
            )
            projection_name = operation.body_projection.name or (
                "openapi:" + operation.operation_id
            )
        lines.extend(
            [
                f"{_projection_constant_name(operation)} = BodyProjection(",
                f"    source={_projection_source_model_name(operation)},",
                f"    target={_projection_target_model_name(operation)},",
                f"    using={using},",
                f"    encoding={_root_body_marker(body.media_type)},",
                f"    name={projection_name!r},",
                ")",
                "",
                "",
            ]
        )
    return lines


def _body_model_field(
    field: Any,
    model_bindings: Mapping[str, str],
    *,
    protection: tuple[str, str] | None = None,
) -> str:
    annotation = _type(field.type_expression, model_bindings)
    metadata: list[str] = []
    if protection is not None:
        flow, source = protection
        metadata.append(f"FromProtection({_protection_constant(flow)}, {source!r})")
    python_name = _request_identifier(field.name)
    if python_name != field.name:
        metadata.append(f"Field(alias={field.name!r})")
    if metadata:
        annotation = f"Annotated[{annotation}, {', '.join(metadata)}]"
    if not field.required and "None" not in annotation:
        annotation = f"{annotation} | None"
    default = "" if field.required else " = None"
    return f"    {python_name}: {annotation}{default}"


def _body_model_name(operation: OperationIR) -> str:
    return f"{_pascal(operation.operation_id)}RequestBody"


def _projection_target_model_name(operation: OperationIR) -> str:
    return f"_{_pascal(operation.operation_id)}ProjectionTarget"


def _projection_source_model_name(operation: OperationIR) -> str:
    if operation.body_projection is not None:
        return f"{_pascal(operation.operation_id)}PublicBody"
    return f"_{_pascal(operation.operation_id)}ProjectionSource"


def _projection_mapper_name(operation: OperationIR) -> str:
    return f"_project_{_request_identifier(operation.operation_id)}_body"


def _projection_constant_name(operation: OperationIR) -> str:
    return f"_{_request_identifier(operation.operation_id).upper()}_BODY_PROJECTION"


def _projection_source_fields(operation: OperationIR) -> tuple[Any, ...]:
    if operation.body_projection is not None:
        return operation.body_projection.source_fields
    body = operation.request_body
    fields = () if body is None or body.wire_fields is None else body.wire_fields
    managed = {
        output.target
        for use in operation.protections
        for output in use.outputs
    }
    return tuple(field for field in fields if field.name not in managed)


def _operation_decorator(
    operation: OperationIR,
    model_bindings: Mapping[str, str],
) -> list[str]:
    verb = operation.method.lower()
    if verb not in {"get", "post", "put", "patch", "delete"}:
        raise ValueError(
            f"operation {operation.operation_id!r} uses unsupported declarative method "
            f"{operation.method!r}"
        )
    successes: list[str] = []
    failures: list[str] = []
    fallback = "None"
    for response in operation.responses:
        selector = _status(response.status)
        representation = _representation(response, model_bindings)
        if response.success:
            successes.append(f"Success({selector}, {representation})")
        else:
            case = (
                f"Error({selector}, {representation}, "
                f"exception={_error_name(operation, response.status)})"
            )
            if response.status == "default":
                fallback = case
            else:
                failures.append(case)
    lines = [
        f"    @api.{verb}(",
        f"        {operation.path!r},",
        f"        operation_id={operation.operation_id!r},",
        f"        responses=Responses[{_result(operation, model_bindings)}](",
        *_response_variants("success", successes),
        *(_response_variants("errors", failures) if failures else []),
        *([f"        fallback={fallback},"] if fallback != "None" else []),
        "        ),",
        *(
            [f"        security={_security_policy(operation)},"]
            if operation.security is not None
            else []
        ),
        *([f"        requires={_requirements(operation)},"] if operation.requires else []),
        *([f"        signing={_signature_uses(operation)},"] if operation.signatures else []),
        *([f"        protections={_protection_uses(operation)},"] if operation.protections else []),
        *(
            [f"        body={_projection_constant_name(operation)},"]
            if operation.protections or operation.body_projection is not None
            else []
        ),
        *([f"        wire={_wire(operation)},"] if operation.wire is not None else []),
        *(
            [f"        idempotent={operation.idempotent!r},"]
            if operation.idempotent is not None
            else []
        ),
        *([f"        tags={operation.tags!r},"] if operation.tags else []),
        "    )",
    ]
    return lines


def _response_variants(name: str, variants: list[str]) -> list[str]:
    if not variants:
        return [f"        {name}=(),"]
    return [
        f"        {name}=(",
        *(f"            {variant}," for variant in variants),
        "        ),",
    ]


def _representation(response: ResponseIR, model_bindings: Mapping[str, str]) -> str:
    if response.type_expression is None:
        return f"Empty(media_type={response.media_type!r})"
    media = response.media_type
    if media is not None and (media == "application/json" or media.endswith("+json")):
        return f"Json({_type(response.type_expression, model_bindings)}, media_type={media!r})"
    if media is not None and media.startswith("text/"):
        return f"Text(media_type={media!r})"
    return f"Bytes(media_type={media!r})"


def _security_scheme(scheme: SecuritySchemeIR) -> str:
    if scheme.kind in {"oauth2", "openIdConnect"}:
        return f"BearerScheme(name={scheme.name!r})"
    if scheme.kind == "http":
        factory = "BearerScheme" if scheme.scheme == "bearer" else "BasicScheme"
        return f"{factory}(name={scheme.name!r})"
    if scheme.location == "cookie":
        return f"CookieScheme({scheme.wire_name!r}, name={scheme.name!r})"
    return f"ApiKeyScheme.{scheme.location}({scheme.wire_name!r}, name={scheme.name!r})"


def _security_policy(operation: OperationIR) -> str:
    if not operation.security:
        return "None"
    alternatives: list[str] = []
    for alternative in operation.security:
        schemes = ", ".join(_constant(name) for name, _scopes in alternative.schemes)
        alternatives.append(f"all_of({schemes})")
    return f"any_of({', '.join(alternatives)})"


def _wire(operation: OperationIR) -> str:
    wire = operation.wire
    if wire is None:
        return "None"
    return (
        "WireOptions("
        f"query_order={wire.query_order!r}, header_order={wire.header_order!r}, "
        f"cookie_order={wire.cookie_order!r}, body_order={wire.body_order!r}, "
        f"exact={wire.exact!r}, protocol={wire.protocol!r})"
    )


def _requirements(operation: OperationIR) -> str:
    values = [
        f"DependencySpec({_constant(item.dependency)}, None, "
        f"{_constant(item.dependency)}_SPEC.bindings, required={item.required!r})"
        for item in operation.requires
    ]
    return _tuple_expression(values)


def _signature_uses(operation: OperationIR) -> str:
    return _tuple_expression([_constant(item) for item in operation.signatures])


def _protection_uses(operation: OperationIR) -> str:
    return _tuple_expression([_protection_constant(item.flow) for item in operation.protections])


def _protection_constant(name: str) -> str:
    return _constant(name)


def _method(
    operation: OperationIR,
    model_bindings: Mapping[str, str],
    *,
    asynchronous: bool,
) -> list[str]:
    name = _identifier(operation.operation_id)
    prefix = "async def" if asynchronous else "def"
    return_type = _result(operation, model_bindings)
    lines = ["", *_operation_decorator(operation, model_bindings)]
    lines.extend([f"    {prefix} {name}(", "        self,", "        *,"])
    names = _request_field_names(operation)
    lines.append("        options: CallOptions | None = None,")
    if names:
        lines.append(f"        **request: Unpack[{_request_model_name(operation)}],")
    lines.append(f"    ) -> {return_type}:")
    lines.append("        raise NotImplementedError")
    return lines


def _status(value: int | str) -> str:
    if isinstance(value, int):
        return str(value)
    if value == "default":
        return "DEFAULT"
    if len(value) == 3 and value.endswith("XX"):
        start = int(value[0]) * 100
        return f"StatusRange({start}, {start + 99})"
    raise ValueError(f"unsupported response status: {value}")


def _errors(operation: OperationIR) -> dict[str, str]:
    grouped: dict[int | str, list[str]] = defaultdict(list)
    for item in operation.responses:
        if not item.success:
            grouped[item.status].append(item.type_expression or "None")
    return {
        _error_name(operation, status): " | ".join(dict.fromkeys(models))
        for status, models in grouped.items()
    }


def _error_name(operation: OperationIR, status: int | str) -> str:
    suffix = "Default" if status == "default" else str(status).replace("X", "x")
    return f"{_pascal(operation.operation_id)}{suffix}Error"


def _result(operation: OperationIR, model_bindings: Mapping[str, str]) -> str:
    values = [item.type_expression or "None" for item in operation.responses if item.success]
    return _type(" | ".join(dict.fromkeys(values)) or "None", model_bindings)


def _model_bindings(
    model_names: frozenset[str], *, reserved: set[str] | frozenset[str] = frozenset()
) -> dict[str, str]:
    all_reserved = _GENERATED_RESERVED_NAMES | reserved
    bindings = {name: name for name in sorted(model_names) if name not in all_reserved}
    used = set(all_reserved) | set(model_names)
    for name in sorted(model_names):
        if name in bindings:
            continue
        base = f"{_pascal(name)}Model"
        alias = base
        suffix = 2
        while alias in used:
            alias = f"{base}{suffix}"
            suffix += 1
        bindings[name] = alias
        used.add(alias)
    return bindings


def _render_model_imports(model_bindings: Mapping[str, str]) -> str:
    if not model_bindings:
        return ""
    lines = ["from .models import ("]
    for model in sorted(model_bindings):
        binding = model_bindings[model]
        imported = model if model == binding else f"{model} as {binding}"
        lines.append(f"    {imported},")
    lines.append(")")
    return "\n".join(lines)


def _render_named_import(module: str, names: list[str]) -> str:
    if not names:
        return ""
    lines = [f"from .{module} import ("]
    lines.extend(f"    {name}," for name in names)
    lines.append(")")
    return "\n".join(lines)


def _type(expression: str, model_bindings: Mapping[str, str]) -> str:
    if not model_bindings:
        return expression
    pattern = r"\b(" + "|".join(re.escape(name) for name in sorted(model_bindings)) + r")\b"

    def replace(match: re.Match[str]) -> str:
        return model_bindings[match.group(0)]

    return re.sub(pattern, replace, expression)


def _constant(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", _identifier(value)).upper()


def _tuple_expression(values: list[str]) -> str:
    if not values:
        return "()"
    return "(" + ", ".join(values) + ",)"


def _pascal(value: str) -> str:
    return "".join(
        part[:1].upper() + part[1:] for part in re.split(r"[^a-zA-Z0-9]+", value) if part
    )


def _identifier(value: str) -> str:
    result = re.sub(r"\W", "_", value)
    if not result or result[0].isdigit():
        result = "_" + result
    return result + "_" if keyword.iskeyword(result) else result
