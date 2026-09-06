"""A WebSocket operation as a frozen model class, like every other operation.

There is no URL to place a field in and no status line to read a case off, so the class is
simpler than an HTTP one: every field is payload, and the event name plus the reply or
message schema is what ``Ws.call`` / ``Ws.subscribe`` / ``Ws.send`` declare.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from eazy_sdk.core.errors import PlanError
from eazy_sdk.crypto import PayloadCrypto, WebSocketEncrypted

from ._messages import WsOperationKind
from .api import _INHERIT_CRYPTO, _InheritCrypto
from .policies import NeverReplay, NeverResubscribe, ResubscribePolicy, WsReplayPolicy
from .schemas import JsonPayload, Messages, OutboundPayload, Replies


@dataclass(frozen=True, slots=True)
class _WsSpec:
    """Everything a WebSocket operation declares besides its fields."""

    kind: WsOperationKind
    discriminator: str
    operation_id: str | None = None
    replay: WsReplayPolicy = field(default_factory=NeverReplay)
    resubscribe: ResubscribePolicy = field(default_factory=NeverResubscribe)
    payload: OutboundPayload | None = None
    """``None`` means the operation class itself is the payload model."""
    replies: Replies | None = None
    messages: Messages | None = None
    crypto: PayloadCrypto | None | _InheritCrypto = _INHERIT_CRYPTO
    encrypted: WebSocketEncrypted | None | _InheritCrypto = _INHERIT_CRYPTO

    def __post_init__(self) -> None:
        if not self.discriminator:
            raise ValueError("WebSocket discriminator cannot be empty")


class _WsOperationBase:
    """What ``op()`` asks of a non-HTTP operation: publish yourself."""

    __slots__ = ()
    __ws__: ClassVar[_WsSpec]

    @classmethod
    def __publish__(cls) -> Any:
        return ws_descriptor(cls)


class WsCall[T](_WsOperationBase):
    """One request/reply exchange; awaiting the operation gives ``T``."""

    __slots__ = ()


class WsSubscribe[T](_WsOperationBase):
    """A stream of ``T``; awaiting the operation gives a ``Subscription[T]``."""

    __slots__ = ()


class WsSend(_WsOperationBase):
    """A message with no reply; awaiting the operation gives ``None``."""

    __slots__ = ()


class Ws:
    """The three verbs of a WebSocket service, as ``Http`` is for HTTP."""

    @staticmethod
    def send(
        discriminator: str,
        /,
        *,
        operation_id: str | None = None,
        replay: WsReplayPolicy | None = None,
        payload: OutboundPayload | None = None,
        crypto: PayloadCrypto | None | _InheritCrypto = _INHERIT_CRYPTO,
        encrypted: WebSocketEncrypted | None | _InheritCrypto = _INHERIT_CRYPTO,
    ) -> _WsSpec:
        return _WsSpec(
            WsOperationKind.SEND,
            discriminator,
            operation_id,
            replay or NeverReplay(),
            NeverResubscribe(),
            payload,
            None,
            None,
            crypto,
            encrypted,
        )

    @staticmethod
    def call(
        discriminator: str,
        /,
        *,
        operation_id: str | None = None,
        replay: WsReplayPolicy | None = None,
        payload: OutboundPayload | None = None,
        replies: Replies | None = None,
        crypto: PayloadCrypto | None | _InheritCrypto = _INHERIT_CRYPTO,
        encrypted: WebSocketEncrypted | None | _InheritCrypto = _INHERIT_CRYPTO,
    ) -> _WsSpec:
        return _WsSpec(
            WsOperationKind.CALL,
            discriminator,
            operation_id,
            replay or NeverReplay(),
            NeverResubscribe(),
            payload,
            replies,
            None,
            crypto,
            encrypted,
        )

    @staticmethod
    def subscribe(
        discriminator: str,
        /,
        *,
        operation_id: str | None = None,
        resubscribe: ResubscribePolicy | None = None,
        payload: OutboundPayload | None = None,
        messages: Messages | None = None,
        crypto: PayloadCrypto | None | _InheritCrypto = _INHERIT_CRYPTO,
        encrypted: WebSocketEncrypted | None | _InheritCrypto = _INHERIT_CRYPTO,
    ) -> _WsSpec:
        return _WsSpec(
            WsOperationKind.SUBSCRIBE,
            discriminator,
            operation_id,
            NeverReplay(),
            resubscribe or NeverResubscribe(),
            payload,
            None,
            messages,
            crypto,
            encrypted,
        )


WS_OPERATION_BASES = (WsCall, WsSubscribe, WsSend)


def is_ws_operation(operation: type[object]) -> bool:
    return issubclass(operation, WS_OPERATION_BASES)


def ws_descriptor(operation: type[object]) -> Any:
    """The router member ``op(SomeWsOperation)`` publishes."""

    from eazy_sdk.models import default_model_adapters

    from .api import _WsOperationDeclaration, _WsOperationDescriptor

    spec = getattr(operation, "__ws__", None)
    if not isinstance(spec, _WsSpec):
        raise PlanError(
            f"operation class {operation.__name__} has no __ws__; assign Ws.call(...), "
            "Ws.subscribe(...) or Ws.send(...)"
        )
    _validate_payload_only(operation)
    declaration = _WsOperationDeclaration(
        spec.operation_id or operation.__name__,
        spec.kind,
        spec.discriminator,
        spec.replay,
        spec.resubscribe,
        spec.payload or JsonPayload(operation),
        spec.replies,
        spec.messages,
    )
    return _WsOperationDescriptor(
        None,
        declaration,
        spec.crypto,
        spec.encrypted,
        operation_type=operation,
        models=default_model_adapters(),
    )


def _validate_payload_only(operation: type[object]) -> None:
    """D-21: a WebSocket operation has nowhere to place a field — it is all payload."""

    from eazy_sdk.compile.input import _PLACEMENT_TYPES
    from eazy_sdk.models.adapters import unwrap_annotated

    for name, annotation in _annotations(operation).items():
        _, metadata = unwrap_annotated(annotation)
        if any(isinstance(item, _PLACEMENT_TYPES) for item in metadata):
            raise PlanError(
                f"WebSocket operation {operation.__name__} cannot place fields; "
                f"the whole class is the payload ({name!r})"
            )


def _annotations(operation: type[object]) -> dict[str, object]:
    from typing import get_type_hints

    return dict(get_type_hints(operation, include_extras=True))


__all__ = ["Ws", "WsCall", "WsSend", "WsSubscribe", "is_ws_operation", "ws_descriptor"]
