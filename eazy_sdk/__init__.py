"""Eazy SDK: declare an API, compose it into an SDK, hand it a client.

What lives here is what an SDK author needs in the first hour: the declaration surface, the
composition surface, the request markers and the response contract. Everything else stays
importable from the module that owns it — ``eazy_sdk.models``, ``eazy_sdk.codecs``,
``eazy_sdk.crypto``, ``eazy_sdk.protection.advanced``, ``eazy_sdk.core.errors`` — because a
second name in the root is duplication, not convenience.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eazy_sdk.api import AsyncApi, SyncApi, api, api_group, op
from eazy_sdk.clients import ClientConfig, Hooks, Resilience, RetryPolicy, Security
from eazy_sdk.dependencies import Inject
from eazy_sdk.handlers import HandlerProfile, TransportError
from eazy_sdk.identity import Identity
from eazy_sdk.operation import Http, HttpOperation
from eazy_sdk.preparation import PreparedCall, PrepareOptions
from eazy_sdk.request import (
    BodyProjection,
    Cookie,
    FormBody,
    Header,
    JsonBody,
    JsonField,
    Path,
    Query,
)
from eazy_sdk.response import (
    Html,
    Json,
    ResponseEnvelope,
    UnexpectedResponseError,
)
from eazy_sdk.root import AsyncRoot, Binding, SyncRoot, bind
from eazy_sdk.sentinels import UNSET, Omittable
from eazy_sdk.serialization import Serialization

__version__ = "0.2.0a5"

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
    "UNSET",
    "AsyncApi",
    "AsyncClient",
    "AsyncRoot",
    "Binding",
    "BodyProjection",
    "Client",
    "ClientConfig",
    "Cookie",
    "FormBody",
    "HandlerProfile",
    "Header",
    "Hooks",
    "Html",
    "Http",
    "HttpOperation",
    "Identity",
    "Inject",
    "Json",
    "JsonBody",
    "JsonField",
    "Omittable",
    "Path",
    "PrepareOptions",
    "PreparedCall",
    "Query",
    "Resilience",
    "ResponseEnvelope",
    "RetryPolicy",
    "Security",
    "Serialization",
    "SyncApi",
    "SyncRoot",
    "TransportError",
    "UnexpectedResponseError",
    "api",
    "api_group",
    "bind",
    "op",
]
