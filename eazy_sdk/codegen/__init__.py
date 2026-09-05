"""Stable runtime surface imported by generated SDK packages."""

from __future__ import annotations

from eazy_sdk.api import AsyncApi, SyncApi
from eazy_sdk.auth import (
    ApiKeyScheme,
    AuthContext,
    BasicScheme,
    BearerScheme,
    CookieScheme,
    SecurityAlternative,
    SecurityPolicy,
    all_of,
    any_of,
)
from eazy_sdk.auth.session_runtime import generated_session_auth, generated_session_scheme
from eazy_sdk.clients import (
    AsyncClient,
    CallOptions,
    Client,
    ClientConfig,
    RetryPolicy,
)
from eazy_sdk.dependencies import (
    DependencyCachePolicy,
    DependencyProvider,
    DependencyRegistry,
    DependencySpec,
    RequestDependency,
    field,
    value,
)
from eazy_sdk.protection.advanced import (
    FromProtection,
    ProtectionBundle,
    SolverRequirement,
    protection_flow,
)
from eazy_sdk.request import (
    BodyProjection,
    BytesBody,
    Cookie,
    Form,
    FormBody,
    Header,
    JsonBody,
    JsonField,
    MultipartBody,
    Part,
    Path,
    Query,
    QueryString,
    ReplayableStreamBody,
    WireOptions,
)
from eazy_sdk.response import (
    DEFAULT,
    ApiError,
    Bytes,
    Empty,
    Error,
    Json,
    Parsed,
    ResponseEnvelope,
    Responses,
    StatusRange,
    Success,
    Text,
)
from eazy_sdk.root import AsyncRoot, Binding, SyncRoot, bind


class Unset:
    __slots__ = ()


UNSET = Unset()

__all__ = [
    "DEFAULT",
    "UNSET",
    "ApiError",
    "ApiKeyScheme",
    "AsyncApi",
    "AsyncClient",
    "AsyncRoot",
    "AuthContext",
    "BasicScheme",
    "BearerScheme",
    "Binding",
    "BodyProjection",
    "Bytes",
    "BytesBody",
    "CallOptions",
    "Client",
    "ClientConfig",
    "Cookie",
    "CookieScheme",
    "DependencyCachePolicy",
    "DependencyProvider",
    "DependencyRegistry",
    "DependencySpec",
    "Empty",
    "Error",
    "Form",
    "FormBody",
    "FromProtection",
    "Header",
    "Json",
    "JsonBody",
    "JsonField",
    "MultipartBody",
    "Parsed",
    "Part",
    "Path",
    "ProtectionBundle",
    "Query",
    "QueryString",
    "ReplayableStreamBody",
    "RequestDependency",
    "ResponseEnvelope",
    "Responses",
    "RetryPolicy",
    "SecurityAlternative",
    "SecurityPolicy",
    "SolverRequirement",
    "StatusRange",
    "Success",
    "SyncApi",
    "SyncRoot",
    "Text",
    "Unset",
    "WireOptions",
    "all_of",
    "any_of",
    "bind",
    "field",
    "generated_session_auth",
    "generated_session_scheme",
    "protection_flow",
    "value",
]
