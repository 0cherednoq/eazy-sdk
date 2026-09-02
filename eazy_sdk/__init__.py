"""Eazy SDK public surface for the compiled prepared-request runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eazy_sdk._internal.errors import (
    BindingError,
    GraphError,
    OperationBindingError,
    PatchError,
    PlanError,
)
from eazy_sdk.api import ApiDefaults, AsyncApi, SyncApi, api
from eazy_sdk.clients import (
    AttemptLimitExceeded,
    ClientConfig,
    RedirectLimitExceeded,
    RetryPolicy,
    UnsafeReplayError,
)
from eazy_sdk.codecs import BodyCodec, DelimitedScalarCodec, ScalarCodec
from eazy_sdk.dependencies import Inject
from eazy_sdk.extraction import (
    CSS,
    ExtractionCompileError,
    ExtractionError,
    Scope,
    XPath,
    parse_html,
)
from eazy_sdk.handlers import CapabilityMismatch, HandlerProfile, TransportFailure
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
    PreparationIncomplete,
    PreparedCall,
    PreparedValue,
    PrepareOptions,
)
from eazy_sdk.response import (
    AmbiguousResponseError,
    Html,
    MalformedResponseError,
    ResponseEnvelope,
    ResponseExtractor,
    UnexpectedResponseError,
)

__version__ = "0.2.0a3"

if TYPE_CHECKING:
    from eazy_sdk.clients import AsyncClient, Client
    from eazy_sdk.sdk import AsyncSdk, SyncSdk, api_group


def __getattr__(name: str) -> Any:
    if name in {"AsyncSdk", "SyncSdk", "api_group"}:
        from eazy_sdk.sdk import AsyncSdk, SyncSdk, api_group

        value = {"AsyncSdk": AsyncSdk, "SyncSdk": SyncSdk, "api_group": api_group}[name]
        globals()[name] = value
        return value
    if name not in {"AsyncClient", "Client"}:
        raise AttributeError(name)
    from eazy_sdk.clients import AsyncClient, Client

    value = AsyncClient if name == "AsyncClient" else Client
    globals()[name] = value
    return value


__all__ = [
    "CSS",
    "AmbiguousModelAdapterError",
    "AmbiguousResponseError",
    "ApiDefaults",
    "AsyncApi",
    "AsyncClient",
    "AsyncSdk",
    "AttemptLimitExceeded",
    "BindingError",
    "BodyCodec",
    "CapabilityMismatch",
    "Client",
    "ClientConfig",
    "DelimitedScalarCodec",
    "ExtractionCompileError",
    "ExtractionError",
    "GraphError",
    "HandlerProfile",
    "Html",
    "Inject",
    "MalformedResponseError",
    "MiddlewareProtocolError",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelAdapterRegistry",
    "ModelField",
    "OperationBindingError",
    "PatchError",
    "PlanError",
    "PreparationIncomplete",
    "PrepareOptions",
    "PreparedCall",
    "PreparedValue",
    "RedirectLimitExceeded",
    "ResponseEnvelope",
    "ResponseExtractor",
    "RetryPolicy",
    "ScalarCodec",
    "Scope",
    "SyncApi",
    "SyncSdk",
    "TransportFailure",
    "UnexpectedResponseError",
    "UnsafeReplayError",
    "UnsupportedModelTypeError",
    "XPath",
    "api",
    "api_group",
    "default_model_adapters",
    "parse_html",
]
