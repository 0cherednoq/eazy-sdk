"""Declarative async WebSocket operations backed by :mod:`eazy_sdk.websocket.runtime`."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Concatenate, ParamSpec, Protocol, TypeVar, cast, overload

from eazy_sdk.core.errors import PlanError
from eazy_sdk.crypto import PayloadCrypto, WebSocketEncrypted

from ._messages import WsOperationKind
from .policies import (
    NeverReplay,
    NeverResubscribe,
    ResubscribePolicy,
    WsReplayPolicy,
)
from .runtime import WsCallOptions
from .schemas import JsonPayload, Messages, OutboundPayload, Replies

P = ParamSpec("P")
T = TypeVar("T")
TApi = TypeVar("TApi")


class _InheritCrypto:
    __slots__ = ()


_INHERIT_CRYPTO = _InheritCrypto()


@dataclass(frozen=True, slots=True)
class WsApiDefaults:
    crypto: PayloadCrypto | None = None
    encrypted: WebSocketEncrypted | None = None


class _WsClient(Protocol):
    async def _execute_operation(
        self,
        declaration: object,
        values: dict[str, object],
        *,
        options: WsCallOptions | None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class _WsOperationDeclaration:
    operation_id: str
    kind: WsOperationKind
    discriminator: str
    replay: WsReplayPolicy
    resubscribe: ResubscribePolicy = field(default_factory=NeverResubscribe)
    payload: OutboundPayload = field(default_factory=lambda: JsonPayload[object]())
    replies: Replies | None = None
    messages: Messages | None = None
    crypto: PayloadCrypto | None = None
    encrypted: WebSocketEncrypted | None = None
    crypto_inherit: bool = True


class _BoundWsOperation[**P, T]:
    def __init__(self, descriptor: _WsOperationDescriptor[Any, P, T], api: AsyncWsApi) -> None:
        self._descriptor = descriptor
        self._api = api
        self.__name__ = descriptor.__name__
        self.__doc__ = descriptor.__doc__
        parameters = tuple(descriptor.signature.parameters.values())
        self.__signature__ = descriptor.signature.replace(
            parameters=parameters[1:] if descriptor.self_parameter is not None else parameters
        )

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        values, options = self._descriptor.bind(self._api, *args, **kwargs)
        result = await self._api._client._execute_operation(
            self._descriptor.resolve(self._api.defaults),
            values,
            options=options,
        )
        return cast(T, result)


class _WsOperationDescriptor[TApi, **P, T]:
    """One WebSocket operation on a router, written as a class or as a decorated method.

    Both forms end here with the same declaration: ``operation_type`` is the class the
    fields were read from, and for a decorated method it is the class the decorator wrote.
    """

    def __init__(
        self,
        function: Callable[..., object] | None,
        declaration: _WsOperationDeclaration,
        crypto: object,
        encrypted: object,
        *,
        operation_type: type[object] | None = None,
        models: object = None,
    ) -> None:
        self.function = function
        self.declaration = declaration
        self.crypto = crypto
        self.encrypted = encrypted
        self.operation_type = operation_type
        self.models = models
        if function is not None:
            self.signature = inspect.signature(function)
            self.self_parameter: str | None = next(iter(self.signature.parameters))
            self.__name__ = function.__name__
            self.__qualname__ = function.__qualname__
            self.__doc__ = function.__doc__
        else:
            owner = cast(type[object], operation_type)
            self.signature = inspect.signature(owner)
            self.self_parameter = None
            self.__name__ = owner.__name__
            self.__qualname__ = owner.__qualname__
            self.__doc__ = owner.__doc__
        self.__signature__ = self.signature

    def resolve(self, defaults: WsApiDefaults) -> _WsOperationDeclaration:
        from dataclasses import replace

        crypto = defaults.crypto if self.crypto is _INHERIT_CRYPTO else self.crypto
        wire = defaults.encrypted if self.encrypted is _INHERIT_CRYPTO else self.encrypted
        return replace(
            self.declaration,
            crypto=cast(PayloadCrypto | None, crypto),
            encrypted=cast(WebSocketEncrypted | None, wire),
            crypto_inherit=self.crypto is _INHERIT_CRYPTO and defaults.crypto is None,
        )

    @overload
    def __get__(self, instance: None, owner: type[TApi]) -> _WsOperationDescriptor[TApi, P, T]: ...

    @overload
    def __get__(
        self,
        instance: TApi,
        owner: type[TApi] | None = None,
    ) -> _BoundWsOperation[P, T]: ...

    def __get__(self, instance: TApi | None, owner: type[TApi] | None = None) -> object:
        if instance is None:
            return self
        if not isinstance(instance, AsyncWsApi):
            raise TypeError("WebSocket operation must be bound to AsyncWsApi")
        return _BoundWsOperation(cast(Any, self), instance)

    def __set_name__(self, owner: type[object], name: str) -> None:
        """D-22: a WebSocket operation belongs to an ``AsyncWsApi`` and to nothing else."""

        if not issubclass(owner, AsyncWsApi):
            raise PlanError(
                f"WebSocket operation {self.__name__} is published on {owner.__name__}; "
                "WebSocket operations belong to AsyncWsApi"
            )

    def bind(
        self,
        api: object,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> tuple[dict[str, object], WsCallOptions | None]:
        if self.self_parameter is None:
            options = kwargs.pop("options", None)
            if options is not None and not isinstance(options, WsCallOptions):
                raise TypeError("options must be WsCallOptions or None")
            # The class constructor applies its own defaults and refuses what it does not
            # declare, so the payload is exactly what the operation says it is.
            request = cast(type[Any], self.operation_type)(*args, **kwargs)
            return self.values_of(request), options
        bound = self.signature.bind(api, *args, **kwargs)
        bound.apply_defaults()
        bound.arguments.pop(self.self_parameter)
        options = bound.arguments.pop("options", None)
        if options is not None and not isinstance(options, WsCallOptions):
            raise TypeError("options must be WsCallOptions or None")
        return dict(bound.arguments), options

    def values_of(self, request: object) -> dict[str, object]:
        """The payload fields of a request value; ``UNSET`` is what "not passed" looks like.

        The HTTP side drops an omitted field the same way, so ``Omittable[T] = UNSET`` means
        one thing on both protocols: the field is not in the message at all.
        """

        from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
        from eazy_sdk.sentinels import Unset

        models = self.models if isinstance(self.models, ModelAdapterRegistry) else None
        registry = models or default_model_adapters()
        owner = cast(type[object], self.operation_type)
        return {
            field.name: value
            for field in registry.fields(owner)
            if not isinstance(value := getattr(request, field.name), Unset)
        }


class AsyncWsApi:
    defaults = WsApiDefaults()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        operation_ids: set[str] = set()
        for name in dir(cls):
            descriptor = inspect.getattr_static(cls, name)
            if not isinstance(descriptor, _WsOperationDescriptor):
                # D-23: an HTTP or RPC operation has a URL and a status line; a WebSocket
                # router has neither, so it cannot carry one.
                if getattr(descriptor, "spec", None) is not None and hasattr(
                    descriptor, "operation_type"
                ):
                    raise PlanError(
                        f"HTTP operation {name} is published on {cls.__name__}; "
                        "an AsyncWsApi carries WebSocket operations"
                    )
                continue
            operation_id = descriptor.declaration.operation_id
            if operation_id in operation_ids:
                raise TypeError(f"duplicate WebSocket operation_id: {operation_id}")
            operation_ids.add(operation_id)

    def __init__(self, client: _WsClient) -> None:
        self._client = client


class _WsOperationDecorator:
    def __init__(
        self,
        kind: WsOperationKind,
        discriminator: str,
        operation_id: str | None,
        replay: WsReplayPolicy,
        resubscribe: ResubscribePolicy,
        payload: OutboundPayload,
        replies: Replies | None,
        messages: Messages | None,
        crypto: object,
        encrypted: object,
    ) -> None:
        if not discriminator:
            raise ValueError("WebSocket discriminator cannot be empty")
        self.kind = kind
        self.discriminator = discriminator
        self.operation_id = operation_id
        self.replay = replay
        self.resubscribe = resubscribe
        self.payload = payload
        self.replies = replies
        self.messages = messages
        self.crypto = crypto
        self.encrypted = encrypted

    def __call__(
        self,
        function: Callable[Concatenate[TApi, P], Awaitable[T]],
    ) -> _WsOperationDescriptor[TApi, P, T]:
        if not inspect.iscoroutinefunction(function):
            raise TypeError("WebSocket operations must be async methods")
        signature = inspect.signature(function)
        if not signature.parameters:
            raise TypeError("WebSocket operation must be an instance method")
        declaration = _WsOperationDeclaration(
            self.operation_id or function.__name__,
            self.kind,
            self.discriminator,
            self.replay,
            self.resubscribe,
            self.payload,
            self.replies,
            self.messages,
        )
        return _WsOperationDescriptor(function, declaration, self.crypto, self.encrypted)


class _WsDecorators:
    def send(
        self,
        discriminator: str,
        *,
        operation_id: str | None = None,
        replay: WsReplayPolicy | None = None,
        payload: OutboundPayload | None = None,
        crypto: PayloadCrypto | None | _InheritCrypto = _INHERIT_CRYPTO,
        encrypted: WebSocketEncrypted | None | _InheritCrypto = _INHERIT_CRYPTO,
    ) -> _WsOperationDecorator:
        return _WsOperationDecorator(
            WsOperationKind.SEND,
            discriminator,
            operation_id,
            replay or NeverReplay(),
            NeverResubscribe(),
            payload or JsonPayload(),
            None,
            None,
            crypto,
            encrypted,
        )

    def call(
        self,
        discriminator: str,
        *,
        operation_id: str | None = None,
        replay: WsReplayPolicy | None = None,
        payload: OutboundPayload | None = None,
        replies: Replies | None = None,
        crypto: PayloadCrypto | None | _InheritCrypto = _INHERIT_CRYPTO,
        encrypted: WebSocketEncrypted | None | _InheritCrypto = _INHERIT_CRYPTO,
    ) -> _WsOperationDecorator:
        return _WsOperationDecorator(
            WsOperationKind.CALL,
            discriminator,
            operation_id,
            replay or NeverReplay(),
            NeverResubscribe(),
            payload or JsonPayload(),
            replies,
            None,
            crypto,
            encrypted,
        )

    def subscribe(
        self,
        discriminator: str,
        *,
        operation_id: str | None = None,
        resubscribe: ResubscribePolicy | None = None,
        payload: OutboundPayload | None = None,
        messages: Messages | None = None,
        crypto: PayloadCrypto | None | _InheritCrypto = _INHERIT_CRYPTO,
        encrypted: WebSocketEncrypted | None | _InheritCrypto = _INHERIT_CRYPTO,
    ) -> _WsOperationDecorator:
        return _WsOperationDecorator(
            WsOperationKind.SUBSCRIBE,
            discriminator,
            operation_id,
            NeverReplay(),
            resubscribe or NeverResubscribe(),
            payload or JsonPayload(),
            None,
            messages,
            crypto,
            encrypted,
        )


ws = _WsDecorators()


__all__ = ["AsyncWsApi", "WsApiDefaults", "ws"]
