"""JSON-RPC 2.0 as one envelope and three response helpers — no second SDK.

A JSON-RPC service is an ordinary HTTP service whose method name travels in the body instead of
the path. Everything else about it — signatures, payload crypto, retries, typed errors — is
already declared the same way, so the only thing missing was the framing. That is what lives
here: a pure function from ``(method, params, id)`` to a structure and back.

The errors deliberately do not get their own path. JSON-RPC answers ``200 OK`` with
``{"error": {...}}``, and :class:`~eazy_sdk.response.Responses` already selects a case by
``condition``; :func:`rpc_result` and :func:`rpc_error` build ordinary ``Success``/``Error`` cases
with that condition filled in, so a typed error model keeps working exactly as it does over REST.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from eazy_sdk.core.errors import EazySdkError
from eazy_sdk.core.kernel import Malformed, ParseAttempt, ParsedValue
from eazy_sdk.crypto import FrozenValue, freeze_value, thaw_value
from eazy_sdk.response.cases import (
    BoundResponseExtractor,
    Error,
    Json,
    ResponseContext,
    Success,
)

from .core import CorrelationKey, InboundMessageKind, ProtocolMessage

type IdOnRetry = Literal["reuse", "regenerate"]


class RpcEnvelopeError(EazySdkError, ValueError):
    """The response is not a JSON-RPC envelope, or answers a request we did not send."""


@dataclass(frozen=True, slots=True)
class JsonRpc:
    """The JSON-RPC 2.0 envelope, declared once on the service that speaks it."""

    path: str = "/"
    version: str = "2.0"
    method: str = "POST"
    id_on_retry: IdOnRetry = "reuse"
    """``reuse`` lets the server recognise a repeat as a duplicate, like an idempotency key."""

    def __post_init__(self) -> None:
        if not self.path.startswith("/"):
            raise ValueError("JsonRpc.path must start with '/'")
        if not self.version:
            raise ValueError("JsonRpc.version must not be empty")

    def new_correlation(self) -> str:
        return str(uuid.uuid4())

    def build_outbound(
        self,
        discriminator: str,
        payload: FrozenValue,
        *,
        correlation: CorrelationKey | None = None,
        channel: object | None = None,
    ) -> FrozenValue:
        envelope: dict[str, object] = {"jsonrpc": self.version}
        if correlation is not None:
            envelope["id"] = correlation.value
        envelope["method"] = discriminator
        params = thaw_value(payload)
        if params is not None:
            envelope["params"] = params
        return freeze_value(envelope)

    def read(self, envelope: FrozenValue) -> ParseAttempt[ProtocolMessage]:
        raw = thaw_value(envelope)
        if not isinstance(raw, Mapping):
            return Malformed(RpcEnvelopeError("JSON-RPC response must be an object"))
        identifier = raw.get("id")
        correlation = (
            CorrelationKey(str(identifier))
            if identifier is not None and str(identifier)
            else None
        )
        if "error" in raw:
            return ParsedValue(
                ProtocolMessage(
                    InboundMessageKind.REPLY,
                    None,
                    freeze_value(raw["error"]),
                    correlation,
                    envelope=envelope,
                )
            )
        if "result" not in raw:
            return Malformed(
                RpcEnvelopeError("JSON-RPC response carries neither 'result' nor 'error'")
            )
        return ParsedValue(
            ProtocolMessage(
                InboundMessageKind.REPLY,
                None,
                freeze_value(raw["result"]),
                correlation,
                envelope=envelope,
            )
        )


def _document(context: ResponseContext[object]) -> Mapping[str, object] | None:
    parsed = context.json
    if parsed.error is not None or not isinstance(parsed.value, Mapping):
        return None
    return cast(Mapping[str, object], parsed.value)


def has_rpc_result(context: ResponseContext[object]) -> bool:
    document = _document(context)
    return document is not None and "result" in document and "error" not in document


def rpc_error_code(context: ResponseContext[object]) -> int | None:
    document = _document(context)
    error = document.get("error") if document is not None else None
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    return code if isinstance(code, int) else None


@dataclass(frozen=True, slots=True)
class _RpcExtractor:
    """Reads one member out of the envelope and checks the reply answers our request."""

    member: Literal["result", "error"]

    @property
    def name(self) -> str:
        return f"json-rpc-{self.member}"

    def bind(self, response: ResponseContext[object]) -> BoundResponseExtractor:
        return _BoundRpcExtractor(response, self.member)


@dataclass(frozen=True, slots=True)
class _BoundRpcExtractor:
    response: ResponseContext[object]
    member: Literal["result", "error"]

    def extract(self, model: type[object]) -> ParseAttempt[object]:
        document = _document(self.response)
        if document is None:
            return Malformed(RpcEnvelopeError("JSON-RPC response must be a JSON object"))
        expected = self.response.operation.correlation
        actual = document.get("id")
        if expected is not None and (actual is None or str(actual) != expected):
            return Malformed(
                RpcEnvelopeError(
                    f"JSON-RPC reply carries id {actual!r}, but the request sent {expected!r}"
                )
            )
        if self.member not in document:
            return Malformed(RpcEnvelopeError(f"JSON-RPC response has no {self.member!r}"))
        return ParsedValue(document[self.member])


RESULT_EXTRACTOR = _RpcExtractor("result")
ERROR_EXTRACTOR = _RpcExtractor("error")


def rpc_result[T](model: type[T], *, status: int = 200) -> Success[T]:
    """The success case of an RPC call: ``result`` is present and ``error`` is not."""

    return Success(status, Json(model, extractor=RESULT_EXTRACTOR), has_rpc_result)


def rpc_error[T](
    code: int,
    model: type[T],
    *,
    status: int = 200,
    exception: Any = None,
) -> Error[T]:
    """One typed JSON-RPC error, selected by its ``code`` — the same `Error` as over REST."""

    def matches(context: ResponseContext[object]) -> bool:
        return rpc_error_code(context) == code

    case = Error(status, Json(model, extractor=ERROR_EXTRACTOR), condition=matches)
    return case if exception is None else Error(case.status, case.response, exception, matches)


def rpc_error_default[T](model: type[T], *, status: int = 200, exception: Any = None) -> Error[T]:
    """Every JSON-RPC error the service did not give its own model.

    It belongs in ``Responses(fallback=…)``, not among ``errors``: as a case it would match the
    same body a coded case matches, and two matching cases are an ambiguity, not a default.
    """

    def matches(context: ResponseContext[object]) -> bool:
        return rpc_error_code(context) is not None

    case = Error(status, Json(model, extractor=ERROR_EXTRACTOR), condition=matches)
    return case if exception is None else Error(case.status, case.response, exception, matches)


__all__ = [
    "IdOnRetry",
    "JsonRpc",
    "RpcEnvelopeError",
    "has_rpc_result",
    "rpc_error",
    "rpc_error_code",
    "rpc_error_default",
    "rpc_result",
]
