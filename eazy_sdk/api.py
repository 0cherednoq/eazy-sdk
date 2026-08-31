"""Declarative sync and async API methods backed by the shared executor."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import (
    Any,
    Concatenate,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
    get_args,
    get_type_hints,
    overload,
)

from eazy_sdk._internal.http_operation import _OperationDeclaration
from eazy_sdk._internal.http_plan import RequestScope
from eazy_sdk._internal.input import inspect_method_input
from eazy_sdk.auth import AuthScheme, SecurityAlternative, SecurityPolicy
from eazy_sdk.clients.base import CallOptions
from eazy_sdk.crypto import CryptoWire, PayloadCrypto
from eazy_sdk.protection import ProtectionRequirement
from eazy_sdk.request import BodyProjection, WireOptions
from eazy_sdk.request.signatures import RequestSignature
from eazy_sdk.response import ResponseEnvelope, Responses

P = ParamSpec("P")
T = TypeVar("T")
TApi = TypeVar("TApi")
TAsyncApi = TypeVar("TAsyncApi", bound="AsyncApi")
TSyncApi = TypeVar("TSyncApi", bound="SyncApi")


class _Inherit:
    __slots__ = ()


_INHERIT = _Inherit()


@dataclass(frozen=True, slots=True)
class ApiDefaults:
    security: AuthScheme[Any] | SecurityAlternative | SecurityPolicy | None = None
    signing: tuple[RequestSignature, ...] = ()
    crypto: PayloadCrypto | None = None
    crypto_wire: CryptoWire | None = None


class _AsyncClient(Protocol):
    async def _execute_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: CallOptions | None,
        with_response: bool,
    ) -> TResult | ResponseEnvelope[TResult, Any]: ...


class _SyncClient(Protocol):
    def _execute_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: CallOptions | None,
        with_response: bool,
    ) -> TResult | ResponseEnvelope[TResult, Any]: ...


class _BoundAsyncOperation[**P, T]:
    def __init__(self, descriptor: _AsyncOperationDescriptor[Any, P, T], api: AsyncApi) -> None:
        self._descriptor = descriptor
        self._api = api
        self.__name__ = descriptor.__name__
        self.__doc__ = descriptor.__doc__
        self.__signature__ = _bound_signature(descriptor.signature)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        values, options = self._descriptor._bind_arguments(self._api, *args, **kwargs)
        result = await self._api._client._execute_operation(
            self._descriptor.resolve(self._api.defaults),
            values,
            options=options,
            with_response=False,
        )
        return cast(T, result)

    async def with_response(
        self,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> ResponseEnvelope[T, Any]:
        values, options = self._descriptor._bind_arguments(self._api, *args, **kwargs)
        result = await self._api._client._execute_operation(
            self._descriptor.resolve(self._api.defaults),
            values,
            options=options,
            with_response=True,
        )
        return cast(ResponseEnvelope[T, Any], result)


class _BoundSyncOperation[**P, T]:
    def __init__(self, descriptor: _SyncOperationDescriptor[Any, P, T], api: SyncApi) -> None:
        self._descriptor = descriptor
        self._api = api
        self.__name__ = descriptor.__name__
        self.__doc__ = descriptor.__doc__
        self.__signature__ = _bound_signature(descriptor.signature)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        values, options = self._descriptor._bind_arguments(self._api, *args, **kwargs)
        result = self._api._client._execute_operation(
            self._descriptor.resolve(self._api.defaults),
            values,
            options=options,
            with_response=False,
        )
        return cast(T, result)

    def with_response(
        self,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> ResponseEnvelope[T, Any]:
        values, options = self._descriptor._bind_arguments(self._api, *args, **kwargs)
        result = self._api._client._execute_operation(
            self._descriptor.resolve(self._api.defaults),
            values,
            options=options,
            with_response=True,
        )
        return cast(ResponseEnvelope[T, Any], result)


class _OperationDescriptorBase[TApi, **P, T]:
    def __init__(
        self,
        declaration: Callable[..., object],
        operation: _OperationDeclaration[T],
        signature: inspect.Signature,
        self_parameter: str,
        unpacked_parameter: str | None,
        security: object,
        signing: object,
        crypto: object,
        crypto_wire: object,
    ) -> None:
        self.declaration = operation
        self.signature = signature
        self.self_parameter = self_parameter
        self.unpacked_parameter = unpacked_parameter
        self.security = security
        self.signing = signing
        self.crypto = crypto
        self.crypto_wire = crypto_wire
        self.__name__ = declaration.__name__
        self.__qualname__ = declaration.__qualname__
        self.__doc__ = declaration.__doc__
        self.__signature__ = signature

    def resolve(self, defaults: ApiDefaults) -> _OperationDeclaration[T]:
        security = defaults.security if self.security is _INHERIT else self.security
        signing = defaults.signing if self.signing is _INHERIT else self.signing
        crypto = defaults.crypto if self.crypto is _INHERIT else self.crypto
        crypto_wire = defaults.crypto_wire if self.crypto_wire is _INHERIT else self.crypto_wire
        if signing is None:
            signing = ()
        elif not isinstance(signing, tuple):
            signing = (signing,)
        return replace(
            self.declaration,
            security=cast(Any, security),
            signing=cast(tuple[RequestSignature, ...], signing),
            crypto=cast(PayloadCrypto | None, crypto),
            crypto_wire=cast(CryptoWire | None, crypto_wire),
            crypto_inherit=self.crypto is _INHERIT and defaults.crypto is None,
        )

    def _bind_arguments(
        self,
        api: object,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> tuple[dict[str, object], CallOptions | None]:
        bound = self.signature.bind(api, *args, **kwargs)
        provided = frozenset(bound.arguments)
        bound.apply_defaults()
        bound.arguments.pop(self.self_parameter)
        options = bound.arguments.pop("options", None)
        if options is not None and not isinstance(options, CallOptions):
            raise TypeError("options must be CallOptions or None")
        values = {
            name: value
            for name, value in bound.arguments.items()
            if name in provided or self.signature.parameters[name].default is not None
        }
        if self.unpacked_parameter is not None:
            unpacked = values.pop(self.unpacked_parameter, {})
            if not isinstance(unpacked, dict):
                raise TypeError("Unpack[TypedDict] arguments must bind to keyword values")
            values.update(unpacked)
        return values, options


class _AsyncOperationDescriptor(_OperationDescriptorBase[TApi, P, T]):
    @overload
    def __get__(
        self, instance: None, owner: type[TApi]
    ) -> _AsyncOperationDescriptor[TApi, P, T]: ...

    @overload
    def __get__(
        self, instance: TApi, owner: type[TApi] | None = None
    ) -> _BoundAsyncOperation[P, T]: ...

    def __get__(self, instance: TApi | None, owner: type[TApi] | None = None) -> object:
        if instance is None:
            return self
        if not isinstance(instance, AsyncApi):
            raise TypeError("async operation must be bound to AsyncApi")
        return _BoundAsyncOperation(cast(Any, self), instance)


class _SyncOperationDescriptor(_OperationDescriptorBase[TApi, P, T]):
    @overload
    def __get__(
        self, instance: None, owner: type[TApi]
    ) -> _SyncOperationDescriptor[TApi, P, T]: ...

    @overload
    def __get__(
        self, instance: TApi, owner: type[TApi] | None = None
    ) -> _BoundSyncOperation[P, T]: ...

    def __get__(self, instance: TApi | None, owner: type[TApi] | None = None) -> object:
        if instance is None:
            return self
        if not isinstance(instance, SyncApi):
            raise TypeError("sync operation must be bound to SyncApi")
        return _BoundSyncOperation(cast(Any, self), instance)


class AsyncApi:
    defaults = ApiDefaults()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _validate_api_class(cls, asynchronous=True)

    def __init__(self, client: _AsyncClient) -> None:
        self._client = client


class SyncApi:
    defaults = ApiDefaults()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _validate_api_class(cls, asynchronous=False)

    def __init__(self, client: _SyncClient) -> None:
        self._client = client


class _OperationDecorator[T]:
    def __init__(
        self,
        method: str,
        path: str,
        *,
        operation_id: str | None,
        responses: Responses[T],
        security: object,
        requires: tuple[object, ...],
        inject: tuple[object, ...],
        signing: object,
        crypto: object,
        crypto_wire: object,
        protections: tuple[ProtectionRequirement[Any], ...],
        body: BodyProjection[Any, Any] | None,
        wire: WireOptions | None,
        tags: tuple[str, ...],
        idempotent: bool | None,
        raw_response: bool,
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.operation_id = operation_id
        self.responses = responses
        self.security = security
        self.requires = requires
        self.inject = inject
        self.signing = signing
        self.crypto = crypto
        self.crypto_wire = crypto_wire
        self.protections = protections
        self.body = body
        self.wire = wire
        self.tags = tags
        self.idempotent = idempotent
        self.raw_response = raw_response

    @overload
    def __call__(
        self,
        declaration: Callable[Concatenate[TAsyncApi, P], Any],
    ) -> _BoundAsyncOperation[P, T]: ...

    @overload
    def __call__(
        self,
        declaration: Callable[Concatenate[TSyncApi, P], Any],
    ) -> _BoundSyncOperation[P, T]: ...

    def __call__(
        self,
        declaration: Callable[Concatenate[TApi, P], Any],
    ) -> object:
        signature = inspect.signature(declaration)
        parameters = tuple(signature.parameters.values())
        if not parameters:
            raise TypeError("an API operation must be an instance method")
        self_parameter = parameters[0].name
        operation_id = self.operation_id or declaration.__name__
        hints = get_type_hints(declaration, include_extras=True)
        result_type = self.responses._result_type
        if result_type is None:
            result_type = hints.get("return")
        if result_type is None:
            raise TypeError(
                f"operation {operation_id!r} cannot infer its result type from responses; "
                "add a return annotation or declare at least one unambiguous success response"
            )
        public_signature = signature.replace(return_annotation=result_type)
        _validate_options(signature, hints, self_parameter, operation_id)
        input_schema = inspect_method_input(
            signature,
            hints,
            operation_id=operation_id,
            path=self.path,
            self_parameter=self_parameter,
            body_projection=self.body,
        )
        unpacked_parameter = next(
            (
                parameter.name
                for parameter in parameters
                if parameter.kind is inspect.Parameter.VAR_KEYWORD
            ),
            None,
        )
        scope = RequestScope(
            path_prefixes=(self.path,),
            methods=frozenset({self.method}),
            operation_ids=frozenset({operation_id}),
        )
        operation: _OperationDeclaration[Any] = _OperationDeclaration(
            operation_id=operation_id,
            method=self.method,
            path=self.path,
            input_fields=input_schema.fields,
            input_schema=input_schema,
            result_type=result_type,
            responses=self.responses,
            requires=self.requires,
            inject=self.inject,
            protections=self.protections,
            body_projection=self.body,
            wire=self.wire,
            scope=scope,
            tags=self.tags,
            idempotent=self.idempotent,
            raw_response=self.raw_response,
        )
        descriptor_type = (
            _AsyncOperationDescriptor
            if inspect.iscoroutinefunction(declaration)
            else _SyncOperationDescriptor
        )
        return descriptor_type(
            declaration,
            operation,
            public_signature,
            self_parameter,
            unpacked_parameter,
            self.security,
            self.signing,
            self.crypto,
            self.crypto_wire,
        )


class _Verb:
    def __init__(self, method: str) -> None:
        self.method = method

    def __call__(
        self,
        path: str,
        *,
        operation_id: str | None = None,
        responses: Responses[T],
        security: (
            AuthScheme[Any] | SecurityAlternative | SecurityPolicy | None | _Inherit
        ) = _INHERIT,
        requires: tuple[object, ...] = (),
        inject: tuple[object, ...] = (),
        signing: tuple[RequestSignature, ...] | RequestSignature | None | _Inherit = _INHERIT,
        crypto: PayloadCrypto | None | _Inherit = _INHERIT,
        crypto_wire: CryptoWire | None | _Inherit = _INHERIT,
        protections: tuple[ProtectionRequirement[Any], ...] = (),
        body: BodyProjection[Any, Any] | None = None,
        wire: WireOptions | None = None,
        tags: tuple[str, ...] = (),
        idempotent: bool | None = None,
        raw_response: bool = False,
    ) -> _OperationDecorator[T]:
        return _OperationDecorator(
            self.method,
            path,
            operation_id=operation_id,
            responses=responses,
            security=security,
            requires=requires,
            inject=inject,
            signing=signing,
            crypto=crypto,
            crypto_wire=crypto_wire,
            protections=protections,
            body=body,
            wire=wire,
            tags=tags,
            idempotent=idempotent,
            raw_response=raw_response,
        )


def _validate_options(
    signature: inspect.Signature,
    hints: dict[str, object],
    self_parameter: str,
    operation_id: str,
) -> None:
    if next(iter(signature.parameters)) != self_parameter:
        raise TypeError(f"operation {operation_id!r} must declare self first")
    parameter = signature.parameters.get("options")
    if parameter is None:
        return
    annotation = hints.get("options")
    if (
        parameter.kind is not inspect.Parameter.KEYWORD_ONLY
        or parameter.default is not None
        or not _is_optional_call_options(annotation)
    ):
        raise TypeError(
            "options must be keyword-only, annotated CallOptions | None and default to None"
        )


def _is_optional_call_options(annotation: object | None) -> bool:
    return annotation is CallOptions or CallOptions in get_args(annotation)


def _validate_api_class(cls: type[object], *, asynchronous: bool) -> None:
    operation_ids: set[str] = set()
    for name in dir(cls):
        descriptor = inspect.getattr_static(cls, name)
        if not isinstance(descriptor, _OperationDescriptorBase):
            continue
        if asynchronous != isinstance(descriptor, _AsyncOperationDescriptor):
            kind = "async" if asynchronous else "sync"
            raise TypeError(f"{kind} API operation {name!r} has the wrong function kind")
        operation_id = descriptor.declaration.operation_id
        if operation_id in operation_ids:
            raise TypeError(f"duplicate operation_id: {operation_id}")
        operation_ids.add(operation_id)


def _bound_signature(signature: inspect.Signature) -> inspect.Signature:
    return signature.replace(parameters=tuple(signature.parameters.values())[1:])


class _ApiNamespace:
    __slots__ = ()

    delete = _Verb("DELETE")
    get = _Verb("GET")
    patch = _Verb("PATCH")
    post = _Verb("POST")
    put = _Verb("PUT")


api = _ApiNamespace()


__all__ = [
    "ApiDefaults",
    "AsyncApi",
    "SyncApi",
    "api",
]
