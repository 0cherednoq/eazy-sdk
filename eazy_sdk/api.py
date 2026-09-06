"""Declarative sync and async API methods backed by the shared executor."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from typing import (
    Any,
    Concatenate,
    ParamSpec,
    Protocol,
    TypedDict,
    TypeVar,
    Unpack,
    cast,
    get_args,
    get_type_hints,
    overload,
)
from urllib.parse import urlsplit

from eazy_sdk.auth import AuthScheme, SecurityAlternative, SecurityPolicy
from eazy_sdk.compile.http_operation import _OperationDeclaration
from eazy_sdk.compile.input import inspect_method_input
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.crypto import PayloadCrypto
from eazy_sdk.identity import (
    Identity,
    _identity_scope,
    _IdentityScope,
    bind_session_lifecycle,
)
from eazy_sdk.policies import CallOptions
from eazy_sdk.preparation import PreparedCall, PrepareOptions
from eazy_sdk.protection.advanced import SolverRequirement
from eazy_sdk.protocols import Envelope
from eazy_sdk.request.signatures import RequestSignature
from eazy_sdk.request.wire import EMPTY_WIRE, Wire
from eazy_sdk.response import Error, Html, Json, ResponseEnvelope, Responses, Success
from eazy_sdk.response.cases import ResponseRepresentation
from eazy_sdk.serialization import Serialization

P = ParamSpec("P")
T = TypeVar("T")
TResult = TypeVar("TResult")
TApi = TypeVar("TApi")
TAsyncApi = TypeVar("TAsyncApi", bound="AsyncApi")
TSyncApi = TypeVar("TSyncApi", bound="SyncApi")


class _Inherit:
    __slots__ = ()


_INHERIT = _Inherit()


class _Missing:
    __slots__ = ()


_MISSING = _Missing()

SERVICE_ATTRIBUTES = (
    "base_url",
    "errors",
    "security",
    "signing",
    "signed",
    "crypto",
    "wire",
    "protocol",
    "allow",
)
"""Class attributes a router (or a service mixin in its MRO) may declare."""


@dataclass(frozen=True, slots=True)
class _ServiceDefaults:
    """Service declaration collected from a root class and a router's MRO.

    ``allow=None`` means the service declares no allowlist; ``allow=()`` allows nothing.
    """

    base_url: str = ""
    security: AuthScheme[Any] | SecurityAlternative | SecurityPolicy | None = None
    signing: tuple[RequestSignature, ...] = ()
    crypto: PayloadCrypto | None = None
    wire: Wire | None = None
    protocol: Envelope | None = None
    """The application-level envelope this service speaks, if it speaks one."""
    errors: tuple[Error[Any], ...] = ()
    allow: tuple[object, ...] | None = None
    signed: bool = False
    """Every operation of this service must carry a signature; unsigned is a declaration error."""

    def extend(self, other: _ServiceDefaults) -> _ServiceDefaults:
        """Merge a more specific declaration over this one (root → router MRO)."""

        return _ServiceDefaults(
            base_url=other.base_url or self.base_url,
            security=self.security if other.security is None else other.security,
            signing=other.signing or self.signing,
            crypto=self.crypto if other.crypto is None else other.crypto,
            wire=other.wire.over(self.wire) if other.wire is not None else self.wire,
            protocol=self.protocol if other.protocol is None else other.protocol,
            errors=(*self.errors, *other.errors),
            allow=self.allow if other.allow is None else other.allow,
            signed=self.signed or other.signed,
        )


_NO_DEFAULTS = _ServiceDefaults()


class _AsyncClient(Protocol):
    async def aclose(self) -> None: ...

    def _scoped(self, graph: Any) -> Any: ...

    async def _execute_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: CallOptions | None,
        with_response: bool,
        identity: _IdentityScope | None,
        serialization: Serialization | None,
    ) -> TResult | ResponseEnvelope[TResult, Any]: ...

    async def _prepare_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: PrepareOptions,
        identity: _IdentityScope | None,
        serialization: Serialization | None,
    ) -> PreparedCall: ...


class _SyncClient(Protocol):
    def close(self) -> None: ...

    def _scoped(self, graph: Any) -> Any: ...

    def _execute_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: CallOptions | None,
        with_response: bool,
        identity: _IdentityScope | None,
        serialization: Serialization | None,
    ) -> PreparedCall | TResult | ResponseEnvelope[TResult, Any]: ...

    def _prepare_operation[TResult](
        self,
        declaration: _OperationDeclaration[TResult],
        values: dict[str, object],
        *,
        options: PrepareOptions,
        identity: _IdentityScope | None,
        serialization: Serialization | None,
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
            self._descriptor.resolve_for(self._api),
            values,
            options=options,
            with_response=False,
            identity=self._api._scope,
            serialization=self._api._serialization,
        )
        return cast(T, result)

    async def with_response(
        self,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> ResponseEnvelope[T, Any]:
        values, options = self._descriptor._bind_arguments(self._api, *args, **kwargs)
        result = await self._api._client._execute_operation(
            self._descriptor.resolve_for(self._api),
            values,
            options=options,
            with_response=True,
            identity=self._api._scope,
            serialization=self._api._serialization,
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
            self._descriptor.resolve_for(self._api),
            values,
            options=options or PrepareOptions(),
            identity=self._api._scope,
            serialization=self._api._serialization,
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
            self._descriptor.resolve_for(self._api),
            values,
            options=options,
            with_response=False,
            identity=self._api._scope,
            serialization=self._api._serialization,
        )
        return cast(T, result)

    def with_response(
        self,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> ResponseEnvelope[T, Any]:
        values, options = self._descriptor._bind_arguments(self._api, *args, **kwargs)
        result = self._api._client._execute_operation(
            self._descriptor.resolve_for(self._api),
            values,
            options=options,
            with_response=True,
            identity=self._api._scope,
            serialization=self._api._serialization,
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
            self._descriptor.resolve_for(self._api),
            values,
            options=options or PrepareOptions(),
            identity=self._api._scope,
            serialization=self._api._serialization,
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
        inherit_errors: bool,
    ) -> None:
        self.declaration = operation
        self.signature = signature
        self.self_parameter = self_parameter
        self.unpacked_parameter = unpacked_parameter
        self.security = security
        self.signing = signing
        self.crypto = crypto
        self.inherit_errors = inherit_errors
        self.__name__ = declaration.__name__
        self.__qualname__ = declaration.__qualname__
        self.__doc__ = declaration.__doc__
        self.__signature__ = signature

    def resolve(self, defaults: _ServiceDefaults = _NO_DEFAULTS) -> _OperationDeclaration[T]:
        security = defaults.security if self.security is _INHERIT else self.security
        signing = defaults.signing if self.signing is _INHERIT else self.signing
        crypto = defaults.crypto if self.crypto is _INHERIT else self.crypto
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
        _validate_allowed(
            defaults.allow,
            self.declaration.operation_id,
            security,
            cast(tuple[RequestSignature, ...], signing),
        )
        if defaults.signed and not signing:
            raise TypeError(
                f"operation {self.declaration.operation_id!r} carries no signature, and its "
                "service requires every operation to be signed"
            )
        envelope = defaults.protocol
        addressing: dict[str, object] = {}
        if self.declaration.discriminator is not None:
            if envelope is None:
                raise TypeError(
                    f"operation {self.declaration.operation_id!r} is declared with @api.rpc, "
                    "and its service declares no protocol envelope"
                )
            addressing = {
                "path": getattr(envelope, "path", "/"),
                "method": getattr(envelope, "method", "POST"),
                "envelope": envelope,
            }
        return replace(
            self.declaration,
            **cast(Any, addressing),
            base_url=defaults.base_url,
            responses=responses,
            security=cast(Any, security),
            signing=cast(tuple[RequestSignature, ...], signing),
            crypto=cast(PayloadCrypto | None, crypto),
            wire=self.declaration.wire.over(defaults.wire),
            crypto_inherit=self.crypto is _INHERIT and defaults.crypto is None,
        )

    def resolve_for(self, api: SyncApi | AsyncApi) -> _OperationDeclaration[T]:
        """Resolved declaration for one bound router, merged once per router instance."""

        cached = api._resolved.get(self)
        if cached is None:
            cached = self.resolve(api._defaults)
            api._resolved[self] = cached
        return cast("_OperationDeclaration[T]", cached)

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


class _ApiBase:
    """Shared router machinery: one client, one service declaration, one session scope."""

    _service_defaults: _ServiceDefaults = _NO_DEFAULTS

    def __init__(
        self,
        client: object,
        *,
        identity: Identity | None = None,
        serialization: Serialization | None = None,
        defaults: _ServiceDefaults | None = None,
        scope: _IdentityScope | None = None,
    ) -> None:
        if identity is not None and scope is not None:
            raise TypeError("a router receives its session scope from one owner only")
        self._client = cast(Any, client)
        self._defaults = type(self)._service_defaults if defaults is None else defaults
        self._scope = scope if scope is not None else _identity_scope(identity)
        self._serialization = serialization if serialization is not None else Serialization()
        self._resolved: dict[object, _OperationDeclaration[Any]] = {}
        if scope is None and identity is not None:
            bind_session_lifecycle(self._scope, client, lambda scoped: type(self)(
                scoped,
                serialization=self._serialization,
                defaults=self._defaults,
                scope=self._scope,
            ))


class AsyncApi(_ApiBase):
    """Asynchronous router: operations plus the service attributes of its own MRO.

    Construct it over an existing client (``UsersApi(client)``); composing several routers
    into one SDK is the job of :class:`~eazy_sdk.root.AsyncRoot`.
    """

    _client: _AsyncClient

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _validate_api_class(cls, asynchronous=True)
        _reject_nested_groups(cls)
        cls._service_defaults = _service_defaults_of(cls)

    def __init__(
        self,
        client: _AsyncClient,
        *,
        identity: Identity | None = None,
        serialization: Serialization | None = None,
        defaults: _ServiceDefaults | None = None,
        scope: _IdentityScope | None = None,
    ) -> None:
        super().__init__(
            client,
            identity=identity,
            serialization=serialization,
            defaults=defaults,
            scope=scope,
        )


class SyncApi(_ApiBase):
    """Synchronous router: operations plus the service attributes of its own MRO.

    Construct it over an existing client (``UsersApi(client)``); composing several routers
    into one SDK is the job of :class:`~eazy_sdk.root.SyncRoot`.
    """

    _client: _SyncClient

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _validate_api_class(cls, asynchronous=False)
        _reject_nested_groups(cls)
        cls._service_defaults = _service_defaults_of(cls)

    def __init__(
        self,
        client: _SyncClient,
        *,
        identity: Identity | None = None,
        serialization: Serialization | None = None,
        defaults: _ServiceDefaults | None = None,
        scope: _IdentityScope | None = None,
    ) -> None:
        super().__init__(
            client,
            identity=identity,
            serialization=serialization,
            defaults=defaults,
            scope=scope,
        )


class _ApiGroup[TGroup: SyncApi | AsyncApi]:
    def __init__(self, api_type: type[TGroup]) -> None:
        self.api_type = api_type
        self.name = ""

    def __set_name__(self, owner: type[object], name: str) -> None:
        self.name = name

    @overload
    def __get__(self, instance: None, owner: type[object]) -> _ApiGroup[TGroup]: ...

    @overload
    def __get__(self, instance: object, owner: type[object] | None = None) -> TGroup: ...

    def __get__(
        self,
        instance: object | None,
        owner: type[object] | None = None,
    ) -> _ApiGroup[TGroup] | TGroup:
        if instance is None:
            return self
        build = getattr(instance, "_build_group", None)
        if build is None:
            raise TypeError("api_group members are declared on SyncRoot/AsyncRoot subclasses")
        return cast(TGroup, build(self))


def api_group[TGroupApi: SyncApi | AsyncApi](
    api_type: type[TGroupApi],
) -> _ApiGroup[TGroupApi]:
    """Declare a lazily bound router member on an SDK root class."""

    if not isinstance(api_type, type) or not issubclass(api_type, SyncApi | AsyncApi):
        raise TypeError("api_group() requires a SyncApi or AsyncApi subclass")
    return _ApiGroup(api_type)


def _reject_nested_groups(cls: type[object]) -> None:
    for name, value in cls.__dict__.items():
        if isinstance(value, _ApiGroup):
            raise TypeError(
                f"router {cls.__name__} declares api_group {name!r}; "
                "api groups belong to a SyncRoot/AsyncRoot composition"
            )


def _declared_service_attribute(cls: type[object], name: str) -> object:
    """The winning declaration of ``name`` in ``cls.__mro__``, or ``_MISSING``.

    Two unrelated bases declaring different values is a declaration error: MRO order must
    not silently pick one service over another.
    """

    declarations = [
        (base, base.__dict__[name])
        for base in cls.__mro__
        if name in base.__dict__
        # A router member named ``signing`` or ``errors`` is a member, not a declaration.
        and not isinstance(base.__dict__[name], _ApiGroup | _OperationDescriptorBase)
    ]
    if not declarations:
        return _MISSING
    winner, value = declarations[0]
    for base, other in declarations[1:]:
        if issubclass(winner, base):
            continue
        if other is value or other == value:
            continue
        raise TypeError(
            f"{cls.__name__} inherits conflicting {name!r} from "
            f"{winner.__name__} and {base.__name__}; declare it once"
        )
    return value


def _service_defaults_of(cls: type[object]) -> _ServiceDefaults:
    """Collect the service declaration of a router or root class from its MRO."""

    values: dict[str, Any] = {}
    for name in SERVICE_ATTRIBUTES:
        declared = _declared_service_attribute(cls, name)
        if isinstance(declared, _Missing):
            continue
        values[name] = _normalize_service_attribute(cls, name, declared)
    return _ServiceDefaults(**values)


def _normalize_service_attribute(cls: type[object], name: str, value: object) -> object:
    if name == "base_url":
        if not isinstance(value, str):
            raise TypeError(f"{cls.__name__}.base_url must be a string")
        return validate_base_url(value, f"{cls.__name__}.base_url")
    if name == "signed":
        if not isinstance(value, bool):
            raise TypeError(f"{cls.__name__}.signed must be a boolean")
        return value
    if name in {"signing", "errors", "allow"}:
        return value if isinstance(value, tuple) else (value,)
    return value


def validate_base_url(value: str, origin: str) -> str:
    """An absolute ``scheme://host`` URL, or empty to inherit the client's address."""

    if not value:
        return ""
    split = urlsplit(value)
    if not split.scheme or not split.netloc:
        raise ValueError(f"{origin} must be an absolute URL, got {value!r}")
    return value


def _security_schemes(
    security: object,
) -> tuple[object, ...]:
    if security is None:
        return ()
    if isinstance(security, SecurityPolicy):
        return tuple(
            scheme for alternative in security.alternatives for scheme in alternative.schemes
        )
    if isinstance(security, SecurityAlternative):
        return security.schemes
    return (security,)


def _validate_allowed(
    allow: tuple[object, ...] | None,
    operation_id: str,
    security: object,
    signing: tuple[RequestSignature, ...],
) -> None:
    if allow is None:
        return
    for item in (*_security_schemes(security), *signing):
        if any(entry is item or entry == item for entry in allow):
            continue
        label = getattr(item, "diagnostic_name", None) or getattr(item, "name", None) or item
        raise TypeError(
            f"operation {operation_id!r} carries {label!r}, which the service allowlist "
            "does not permit"
        )


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
        protections: tuple[SolverRequirement[Any, Any], ...],
        wire: Wire,
        tags: tuple[str, ...],
        idempotent: bool | None,
        raw_response: bool,
        inherit_errors: bool,
        singular_response: bool,
        discriminator: str | None = None,
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.discriminator = discriminator
        self.operation_id = operation_id
        self.responses = responses
        self.security = security
        self.requires = requires
        self.inject = inject
        self.signing = signing
        self.crypto = crypto
        self.protections = protections
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
            body_projection=self.wire.projection,
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
            wire=self.wire,
            scope=scope,
            tags=self.tags,
            idempotent=self.idempotent,
            raw_response=self.raw_response,
            discriminator=self.discriminator,
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
    protections: tuple[SolverRequirement[Any, Any], ...]
    wire: Wire
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
        protections: tuple[SolverRequirement[Any, Any], ...] = (),
        wire: Wire = EMPTY_WIRE,
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
        protections: tuple[SolverRequirement[Any, Any], ...] = (),
        wire: Wire = EMPTY_WIRE,
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
        protections: tuple[SolverRequirement[Any, Any], ...] = (),
        wire: Wire = EMPTY_WIRE,
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
            protections=protections,
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
    envelope = _service_defaults_of(cls).protocol
    for name in dir(cls):
        descriptor = inspect.getattr_static(cls, name)
        if not isinstance(descriptor, _OperationDescriptorBase):
            continue
        if descriptor.declaration.discriminator is not None and envelope is None:
            raise TypeError(
                f"operation {descriptor.declaration.operation_id!r} is declared with @api.rpc, "
                "and its service declares no protocol envelope"
            )
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

    def rpc(
        self,
        discriminator: str,
        *,
        responses: Responses[T],
        **kwargs: Unpack[_OperationOptions],
    ) -> _OperationDecorator[T]:
        """One operation of a service whose method name travels in the body, not the path.

        Not a second family of decorators: every other argument is the one ``api.post`` takes
        and means the same thing. The URL is not repeated here because it belongs to the
        service's envelope, declared once as the router's ``protocol`` attribute.
        """

        decorator = cast(
            _OperationDecorator[T],
            _Verb("POST")("/", responses=responses, **kwargs),
        )
        decorator.discriminator = discriminator
        return decorator

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
    "AsyncApi",
    "SyncApi",
    "api",
    "api_group",
]
