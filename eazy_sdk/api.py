"""Declarative sync and async API methods backed by the shared executor."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from typing import (
    TYPE_CHECKING,
    Any,
    Concatenate,
    ParamSpec,
    Protocol,
    Self,
    TypedDict,
    TypeVar,
    Unpack,
    cast,
    get_args,
    get_type_hints,
    overload,
)

from eazy_sdk.auth import AuthScheme, SecurityAlternative, SecurityPolicy
from eazy_sdk.compile.http_operation import _OperationDeclaration
from eazy_sdk.compile.input import inspect_method_input
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.crypto import CryptoWire, PayloadCrypto
from eazy_sdk.handlers import HandlerProfile
from eazy_sdk.policies import CallOptions
from eazy_sdk.preparation import PreparedCall, PrepareOptions
from eazy_sdk.protection.advanced import SolverRequirement
from eazy_sdk.request import BodyProjection, WireOptions
from eazy_sdk.request.signatures import RequestSignature
from eazy_sdk.response import Error, Html, Json, ResponseEnvelope, Responses, Success
from eazy_sdk.response.cases import ResponseRepresentation

if TYPE_CHECKING:
    from zapros import AsyncBaseHandler, BaseHandler

    from eazy_sdk.clients import ClientConfig

P = ParamSpec("P")
T = TypeVar("T")
TResult = TypeVar("TResult")
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
    errors: tuple[Error[Any], ...] = ()


class _AsyncClient(Protocol):
    def bind_sdk[TSdk](self, sdk_factory: Callable[[Any], TSdk]) -> TSdk: ...

    async def aclose(self) -> None: ...

    async def _execute_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: CallOptions | None,
        with_response: bool,
    ) -> TResult | ResponseEnvelope[TResult, Any]: ...

    async def _prepare_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: PrepareOptions,
    ) -> PreparedCall: ...


class _SyncClient(Protocol):
    def bind_sdk[TSdk](self, sdk_factory: Callable[[Any], TSdk]) -> TSdk: ...

    def close(self) -> None: ...

    def _execute_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: CallOptions | None,
        with_response: bool,
    ) -> TResult | ResponseEnvelope[TResult, Any]: ...

    def _prepare_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: PrepareOptions,
    ) -> PreparedCall: ...


class _BoundAsyncOperation[**P, T]:
    def __init__(self, descriptor: _AsyncOperationDescriptor[Any, P, T], api: AsyncApi) -> None:
        self._descriptor = descriptor
        self._api = api
        self.__name__ = descriptor.__name__
        self.__doc__ = descriptor.__doc__
        self.__signature__ = _bound_signature(descriptor.signature)

    @property
    def declaration(self) -> _OperationDeclaration[T]:
        """Underlying declaration, so a bound method can serve as acquire/verify reference."""

        return self._descriptor.declaration

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

    async def prepare(
        self,
        *args: Any,
        options: PrepareOptions | None = None,
        **kwargs: Any,
    ) -> PreparedCall:
        values, _ = self._descriptor._bind_arguments(self._api, *args, **kwargs)
        return await self._api._client._prepare_operation(
            self._descriptor.resolve(self._api.defaults),
            values,
            options=options or PrepareOptions(),
        )


class _BoundSyncOperation[**P, T]:
    def __init__(self, descriptor: _SyncOperationDescriptor[Any, P, T], api: SyncApi) -> None:
        self._descriptor = descriptor
        self._api = api
        self.__name__ = descriptor.__name__
        self.__doc__ = descriptor.__doc__
        self.__signature__ = _bound_signature(descriptor.signature)

    @property
    def declaration(self) -> _OperationDeclaration[T]:
        """Underlying declaration, so a bound method can serve as acquire/verify reference."""

        return self._descriptor.declaration

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

    def prepare(
        self,
        *args: Any,
        options: PrepareOptions | None = None,
        **kwargs: Any,
    ) -> PreparedCall:
        values, _ = self._descriptor._bind_arguments(self._api, *args, **kwargs)
        return self._api._client._prepare_operation(
            self._descriptor.resolve(self._api.defaults),
            values,
            options=options or PrepareOptions(),
        )


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
        inherit_errors: bool,
    ) -> None:
        self.declaration = operation
        self.signature = signature
        self.self_parameter = self_parameter
        self.unpacked_parameter = unpacked_parameter
        self.security = security
        self.signing = signing
        self.crypto = crypto
        self.crypto_wire = crypto_wire
        self.inherit_errors = inherit_errors
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
        responses = cast(Responses[T], self.declaration.responses)
        if self.inherit_errors and defaults.errors:
            responses = Responses(
                success=cast(tuple[Success[T], ...], responses.success),
                errors=(*defaults.errors, *responses.errors),
                fallback=responses.fallback,
            )
        return replace(
            self.declaration,
            responses=responses,
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
        projection_sources = {
            field.python_name
            for field in self.declaration.input_fields
            if field.is_projection_source
        }
        values = {
            name: value
            for name, value in bound.arguments.items()
            if name in provided
            or self.signature.parameters[name].default is not None
            or name in projection_sources
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
    """Asynchronous API class: operations, nested ``api_group()`` members, optional ownership.

    Construct it over an existing client (``UsersApi(client)``) or let it own one via
    ``from_handler()``/``from_client(..., owns_client=True)``; an owning root closes the client
    in ``aclose()`` and ``async with``.
    """

    defaults = ApiDefaults()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _validate_api_class(cls, asynchronous=True)
        _validate_groups(cls, asynchronous=True)

    def __init__(self, client: _AsyncClient, *, owns_client: bool = False) -> None:
        self._client = client
        self._owns_client = owns_client
        self._closed = False

    @classmethod
    def from_client(cls, client: _AsyncClient, *, owns_client: bool = False) -> Self:
        """Bind this API class as the client's scoped SDK root."""

        root = client.bind_sdk(lambda scoped: cls(scoped, owns_client=False))
        root._owns_client = owns_client
        return root

    @classmethod
    def from_handler(
        cls,
        *,
        handler: AsyncBaseHandler,
        base_url: str = "",
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
    ) -> Self:
        from eazy_sdk.clients import AsyncClient

        client = AsyncClient(
            base_url=base_url,
            handler=handler,
            config=config,
            owns_handler=owns_handler,
            profile=profile,
        )
        return cls.from_client(client, owns_client=True)

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_client:
                await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


class SyncApi:
    """Synchronous API class: operations, nested ``api_group()`` members, optional ownership.

    Construct it over an existing client (``UsersApi(client)``) or let it own one via
    ``from_handler()``/``from_client(..., owns_client=True)``; an owning root closes the client
    in ``close()`` and ``with``.
    """

    defaults = ApiDefaults()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _validate_api_class(cls, asynchronous=False)
        _validate_groups(cls, asynchronous=False)

    def __init__(self, client: _SyncClient, *, owns_client: bool = False) -> None:
        self._client = client
        self._owns_client = owns_client
        self._closed = False

    @classmethod
    def from_client(cls, client: _SyncClient, *, owns_client: bool = False) -> Self:
        """Bind this API class as the client's scoped SDK root."""

        root = client.bind_sdk(lambda scoped: cls(scoped, owns_client=False))
        root._owns_client = owns_client
        return root

    @classmethod
    def from_handler(
        cls,
        *,
        handler: BaseHandler,
        base_url: str = "",
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
    ) -> Self:
        from eazy_sdk.clients import Client

        client = Client(
            base_url=base_url,
            handler=handler,
            config=config,
            owns_handler=owns_handler,
            profile=profile,
        )
        return cls.from_client(client, owns_client=True)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_client:
                self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class _ApiGroup[TGroup: SyncApi | AsyncApi]:
    def __init__(self, api_type: type[TGroup]) -> None:
        self.api_type = api_type
        self.name = ""

    def __set_name__(self, owner: type[object], name: str) -> None:
        self.name = name

    @overload
    def __get__(self, instance: None, owner: type[object]) -> _ApiGroup[TGroup]: ...

    @overload
    def __get__(
        self, instance: SyncApi | AsyncApi, owner: type[object] | None = None
    ) -> TGroup: ...

    def __get__(
        self,
        instance: SyncApi | AsyncApi | None,
        owner: type[object] | None = None,
    ) -> _ApiGroup[TGroup] | TGroup:
        if instance is None:
            return self
        cached = instance.__dict__.get(self.name)
        if cached is None:
            cached = self.api_type(cast(Any, instance._client))
            instance.__dict__[self.name] = cached
        return cast(TGroup, cached)


def api_group[TGroupApi: SyncApi | AsyncApi](
    api_type: type[TGroupApi],
) -> _ApiGroup[TGroupApi]:
    """Declare a lazily bound nested API group on an API class."""

    return _ApiGroup(api_type)


def _validate_groups(root: type[object], *, asynchronous: bool) -> None:
    expected = AsyncApi if asynchronous else SyncApi
    for name, value in root.__dict__.items():
        if isinstance(value, _ApiGroup) and not issubclass(value.api_type, expected):
            kind = "async" if asynchronous else "sync"
            raise TypeError(f"{kind} API group {name!r} uses the wrong API kind")


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
        protections: tuple[SolverRequirement[Any, Any], ...],
        body: BodyProjection[Any, Any] | None,
        wire: WireOptions | None,
        tags: tuple[str, ...],
        idempotent: bool | None,
        raw_response: bool,
        inherit_errors: bool,
        singular_response: bool,
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
        self.inherit_errors = inherit_errors
        self.singular_response = singular_response

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
        if self.singular_response:
            annotated_result = hints.get("return")
            if annotated_result is None:
                raise TypeError(
                    f"operation {operation_id!r} using response= requires a return annotation"
                )
            success = self.responses.success[0]
            representation = success.response
            if isinstance(representation, Json | Html):
                if representation.model is not None and representation.model != annotated_result:
                    raise TypeError(
                        f"operation {operation_id!r} response model must match "
                        "its return annotation"
                    )
                representation = replace(representation, model=annotated_result)
                self.responses = Responses(
                    success=cast(
                        tuple[Success[T], ...],
                        (replace(success, response=representation),),
                    ),
                    errors=self.responses.errors,
                    fallback=self.responses.fallback,
                )
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
            self.inherit_errors,
        )


class _SingularOperationDecorator:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._inner = _OperationDecorator[Any](*args, **kwargs)

    @overload
    def __call__(
        self,
        declaration: Callable[
            Concatenate[TAsyncApi, P], Coroutine[Any, Any, TResult]
        ],
    ) -> _AsyncOperationDescriptor[TAsyncApi, P, TResult]: ...

    @overload
    def __call__(
        self,
        declaration: Callable[Concatenate[TSyncApi, P], TResult],
    ) -> _SyncOperationDescriptor[TSyncApi, P, TResult]: ...

    def __call__(
        self,
        declaration: Callable[Concatenate[TApi, P], Any],
    ) -> object:
        return self._inner(cast(Any, declaration))


class _OperationOptions(TypedDict, total=False):
    operation_id: str | None
    security: object
    requires: tuple[object, ...]
    inject: tuple[object, ...]
    signing: object
    crypto: PayloadCrypto | None | _Inherit
    crypto_wire: CryptoWire | None | _Inherit
    protections: tuple[SolverRequirement[Any, Any], ...]
    body: BodyProjection[Any, Any] | None
    wire: WireOptions | None
    tags: tuple[str, ...]
    idempotent: bool | None
    raw_response: bool
    errors: tuple[Error[Any], ...]
    inherit_errors: bool


class _Verb:
    def __init__(self, method: str) -> None:
        if re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", method) is None:
            raise ValueError(f"invalid HTTP method token: {method!r}")
        self.method = method.upper()

    @overload
    def __call__(
        self,
        path: str,
        *,
        operation_id: str | None = None,
        responses: Responses[T],
        response: None = None,
        errors: tuple[Error[Any], ...] = (),
        inherit_errors: bool = True,
        security: object = _INHERIT,
        requires: tuple[object, ...] = (),
        inject: tuple[object, ...] = (),
        signing: object = _INHERIT,
        crypto: PayloadCrypto | None | _Inherit = _INHERIT,
        crypto_wire: CryptoWire | None | _Inherit = _INHERIT,
        protections: tuple[SolverRequirement[Any, Any], ...] = (),
        body: BodyProjection[Any, Any] | None = None,
        wire: WireOptions | None = None,
        tags: tuple[str, ...] = (),
        idempotent: bool | None = None,
        raw_response: bool = False,
    ) -> _OperationDecorator[T]: ...

    @overload
    def __call__(
        self,
        path: str,
        *,
        operation_id: str | None = None,
        response: ResponseRepresentation[Any],
        responses: None = None,
        errors: tuple[Error[Any], ...] = (),
        inherit_errors: bool = True,
        security: object = _INHERIT,
        requires: tuple[object, ...] = (),
        inject: tuple[object, ...] = (),
        signing: object = _INHERIT,
        crypto: PayloadCrypto | None | _Inherit = _INHERIT,
        crypto_wire: CryptoWire | None | _Inherit = _INHERIT,
        protections: tuple[SolverRequirement[Any, Any], ...] = (),
        body: BodyProjection[Any, Any] | None = None,
        wire: WireOptions | None = None,
        tags: tuple[str, ...] = (),
        idempotent: bool | None = None,
        raw_response: bool = False,
    ) -> _SingularOperationDecorator: ...

    def __call__(
        self,
        path: str,
        *,
        operation_id: str | None = None,
        responses: Responses[T] | None = None,
        response: ResponseRepresentation[T] | None = None,
        errors: tuple[Error[Any], ...] = (),
        inherit_errors: bool = True,
        security: object = _INHERIT,
        requires: tuple[object, ...] = (),
        inject: tuple[object, ...] = (),
        signing: object = _INHERIT,
        crypto: PayloadCrypto | None | _Inherit = _INHERIT,
        crypto_wire: CryptoWire | None | _Inherit = _INHERIT,
        protections: tuple[SolverRequirement[Any, Any], ...] = (),
        body: BodyProjection[Any, Any] | None = None,
        wire: WireOptions | None = None,
        tags: tuple[str, ...] = (),
        idempotent: bool | None = None,
        raw_response: bool = False,
    ) -> _OperationDecorator[Any] | _SingularOperationDecorator:
        if responses is not None and response is not None:
            raise TypeError("response= and responses= are mutually exclusive")
        if responses is None and response is None:
            raise TypeError("declare exactly one of response= or responses=")
        normalized = responses
        if response is not None:
            normalized = _singular_responses(response, errors)
        elif errors:
            assert responses is not None
            normalized = Responses(
                success=cast(tuple[Success[T], ...], responses.success),
                errors=(*responses.errors, *errors),
                fallback=responses.fallback,
            )
        assert normalized is not None
        decorator_type = (
            _SingularOperationDecorator if response is not None else _OperationDecorator
        )
        return decorator_type(
            self.method,
            path,
            operation_id=operation_id,
            responses=normalized,
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
            inherit_errors=inherit_errors,
            singular_response=response is not None,
        )


def _singular_responses[T](
    response: ResponseRepresentation[T],
    errors: tuple[Error[Any], ...],
) -> Responses[T]:
    status = response.status if isinstance(response, Json | Html) else 200
    condition = response.when if isinstance(response, Json | Html) else None
    return Responses(success=(Success(status, response, condition),), errors=errors)


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
    head = _Verb("HEAD")
    options = _Verb("OPTIONS")
    patch = _Verb("PATCH")
    post = _Verb("POST")
    put = _Verb("PUT")
    trace = _Verb("TRACE")

    @overload
    def request(
        self,
        method: str,
        path: str,
        *,
        responses: Responses[T],
        response: None = None,
        **kwargs: Unpack[_OperationOptions],
    ) -> _OperationDecorator[T]: ...

    @overload
    def request(
        self,
        method: str,
        path: str,
        *,
        response: ResponseRepresentation[Any],
        responses: None = None,
        **kwargs: Unpack[_OperationOptions],
    ) -> _SingularOperationDecorator: ...

    def request(
        self, method: str, path: str, **kwargs: Any
    ) -> _OperationDecorator[Any] | _SingularOperationDecorator:
        return cast(
            _OperationDecorator[Any] | _SingularOperationDecorator,
            cast(Any, _Verb(method))(path, **kwargs),
        )


api = _ApiNamespace()


__all__ = [
    "ApiDefaults",
    "AsyncApi",
    "SyncApi",
    "api",
    "api_group",
]
