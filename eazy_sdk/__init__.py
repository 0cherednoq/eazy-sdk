"""Eazy SDK public surface for the compiled prepared-request runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eazy_sdk.api import ApiDefaults, AsyncApi, SyncApi, api, api_group
from eazy_sdk.clients import (
    AttemptLimitError,
    ClientConfig,
    RedirectLimitError,
    RetryPolicy,
    UnsafeReplayError,
)
from eazy_sdk.codecs import BodyCodec, DelimitedScalarCodec, ScalarCodec
from eazy_sdk.core.errors import (
    BindingError,
    GraphError,
    OperationBindingError,
    PatchError,
    PlanError,
)
from eazy_sdk.dependencies import Inject
from eazy_sdk.handlers import CapabilityMismatchError, HandlerProfile, TransportError
from eazy_sdk.middleware import MiddlewareProtocolError
from eazy_sdk.models import (
    AmbiguousModelAdapterError,
    ModelAdapter,
    ModelAdapterError,
    ModelAdapterRegistry,
    ModelField,
    UnsupportedModelTypeError,
    default_model_adapters,
)
from eazy_sdk.preparation import (
    PreparationIncompleteError,
    PreparedCall,
    PreparedValue,
    PrepareOptions,
)
from eazy_sdk.request import (
    BodyProjection,
    Cookie,
    FormBody,
    Header,
    JsonBody,
    Path,
    Query,
)
from eazy_sdk.response import (
    AmbiguousResponseError,
    Bytes,
    Error,
    Html,
    Json,
    MalformedResponseError,
    ResponseEnvelope,
    ResponseExtractor,
    Responses,
    Success,
    Text,
    UnexpectedResponseError,
)

__version__ = "0.2.0a3"

if TYPE_CHECKING:
    from eazy_sdk.clients import AsyncClient, Client


def __getattr__(name: str) -> Any:
    if name not in {"AsyncClient", "Client"}:
        raise AttributeError(name)
    from eazy_sdk.clients import AsyncClient, Client

    value = AsyncClient if name == "AsyncClient" else Client
    globals()[name] = value
    return value


__all__ = [
    "AmbiguousModelAdapterError",
    "AmbiguousResponseError",
    "ApiDefaults",
    "AsyncApi",
    "AsyncClient",
    "AttemptLimitError",
    "BindingError",
    "BodyCodec",
    "BodyProjection",
    "Bytes",
    "CapabilityMismatchError",
    "Client",
    "ClientConfig",
    "Cookie",
    "DelimitedScalarCodec",
    "Error",
    "FormBody",
    "GraphError",
    "HandlerProfile",
    "Header",
    "Html",
    "Inject",
    "Json",
    "JsonBody",
    "MalformedResponseError",
    "MiddlewareProtocolError",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelAdapterRegistry",
    "ModelField",
    "OperationBindingError",
    "PatchError",
    "Path",
    "PlanError",
    "PreparationIncompleteError",
    "PrepareOptions",
    "PreparedCall",
    "PreparedValue",
    "Query",
    "RedirectLimitError",
    "ResponseEnvelope",
    "ResponseExtractor",
    "Responses",
    "RetryPolicy",
    "ScalarCodec",
    "Success",
    "SyncApi",
    "Text",
    "TransportError",
    "UnexpectedResponseError",
    "UnsafeReplayError",
    "UnsupportedModelTypeError",
    "api",
    "api_group",
    "default_model_adapters",
]
