"""Declarative sync and async API routers backed by the shared executor.

An operation is a class (:class:`~eazy_sdk.operation.HttpOperation`) published on a router
with :func:`op`. The decorator form ``@api.get(...)`` is a short spelling of the same thing:
it synthesizes the class from the function signature and hands it to the same descriptor.
Nothing downstream — compiler, executor, codegen — knows which form the author wrote.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import AsyncIterator, Callable, Coroutine, Hashable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import (
    Any,
    Concatenate,
    ParamSpec,
    Protocol,
    TypeVar,
    Unpack,
    cast,
    get_args,
    get_type_hints,
    overload,
    runtime_checkable,
)
from urllib.parse import urlsplit

from eazy_sdk.auth import AuthScheme, SecurityAlternative, SecurityPolicy
from eazy_sdk.compile.http_operation import _OperationDeclaration
from eazy_sdk.compile.input import MethodInputSchema, inspect_operation_input
from eazy_sdk.core.errors import PlanError
from eazy_sdk.core.http import RequestLocation
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.crypto import PayloadCrypto
from eazy_sdk.identity import (
    Identity,
    _identity_scope,
    _IdentityScope,
    bind_session_lifecycle,
)
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
from eazy_sdk.operation import (
    _INHERIT,
    Http,
    HttpOperation,
    _HttpOptions,
    _HttpSpec,
    _Inherit,
    generic_argument,
)
from eazy_sdk.pagination import Pagination, check_max_pages, next_changes, validate_declaration
from eazy_sdk.policies import CallOptions
from eazy_sdk.preparation import PreparedCall, PrepareOptions
from eazy_sdk.protocols import Envelope
from eazy_sdk.protocols.operation import Rpc, RpcOperation, rpc_responses
from eazy_sdk.request.signatures import RequestSignature
from eazy_sdk.request.wire import Wire
from eazy_sdk.response import Error, ResponseEnvelope, Responses, Success
from eazy_sdk.response._mapping import (
    ErrorsMapping,
    error_cases,
    normalize_responses,
    result_type_of,
)
from eazy_sdk.sentinels import UNSET, Omittable, Unset
from eazy_sdk.serialization import Serialization

P = ParamSpec("P")
T = TypeVar("T")
TResult = TypeVar("TResult")
TAsyncApi = TypeVar("TAsyncApi", bound="AsyncApi")
TSyncApi = TypeVar("TSyncApi", bound="SyncApi")


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
    "unwrap",
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
    errors: tuple[ErrorsMapping | Error[Any], ...] = ()
    """Error declarations as written (``Error`` cases or ``{status: spec}`` mappings)."""
    unwrap: str | None = None
    """JSON pointer to the payload inside this service's success envelope."""
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
            unwrap=self.unwrap if other.unwrap is None else other.unwrap,
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


class _BoundOperation[**P, T]:
    """What a router instance hands out for an operation: the class, bound to one client."""

    def __init__(self, descriptor: _OperationDescriptor[P, T], api: _ApiBase) -> None:
        self._descriptor = descriptor
        self._api = api
        self.__name__ = descriptor.__name__
        self.__doc__ = descriptor.__doc__
        self.__signature__ = descriptor.bound_signature()

    @property
    def Operation(self) -> type[HttpOperation[T]]:
        """The operation class: what ``request()`` builds and ``send()`` accepts."""

        return self._descriptor.operation_type

    @property
    def declaration(self) -> _OperationDeclaration[T]:
        """Underlying declaration, so a bound method can serve as acquire/verify reference."""

        return self._descriptor.declaration

    def request(self, *args: P.args, **kwargs: P.kwargs) -> HttpOperation[T]:
        """Build the request value without sending it."""

        return self._descriptor.operation_type(*args, **kwargs)

    def evolve(self, request: HttpOperation[T], /, **changes: object) -> HttpOperation[T]:
        """A copy of ``request`` with some fields replaced, through the model's own library."""

        self._descriptor.check_request(request)
        return self._api._serialization.models.evolve(request, **changes)

    # -- pagination --------------------------------------------------------------------

    def _strategy(self, max_pages: int | None) -> Pagination[T]:
        """The declared ``__pages__``, or D-52-04; ``max_pages`` is checked here too (D-52-06)."""

        strategy = self._descriptor.pages
        if strategy is None:
            raise PlanError(f"pages() requires __pages__ on {self.Operation.__name__}")
        check_max_pages(max_pages)
        return strategy

    def _bind(self, *args: Any, **kwargs: Any) -> tuple[dict[str, object], CallOptions | None]:
        return self._descriptor._bind_arguments(self._api, *args, **kwargs)

    def _values(self, request: HttpOperation[T]) -> dict[str, object]:
        self._descriptor.check_request(request)
        return self._descriptor._values_of(request, self._descriptor.resolve_for(self._api))


class _BoundAsyncOperation[**P, T](_BoundOperation[P, T]):
    _api: AsyncApi

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        values, options = self._bind(*args, **kwargs)
        return await self._execute(values, options)

    async def with_response(
        self,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> ResponseEnvelope[T, Any]:
        values, options = self._bind(*args, **kwargs)
        return await self._execute_with_response(values, options)

    async def prepare(
        self,
        *args: Any,
        options: PrepareOptions | None = None,
        **kwargs: Any,
    ) -> PreparedCall:
        values, _ = self._bind(*args, **kwargs)
        return await self._api._client._prepare_operation(
            self._descriptor.resolve_for(self._api),
            values,
            options=options or PrepareOptions(),
            identity=self._api._scope,
            serialization=self._api._serialization,
        )

    async def send(
        self, request: HttpOperation[T], /, *, options: CallOptions | None = None
    ) -> T:
        """Send a request value built by :meth:`request` or :meth:`evolve`."""

        return await self._execute(self._values(request), options)

    async def send_with_response(
        self, request: HttpOperation[T], /, *, options: CallOptions | None = None
    ) -> ResponseEnvelope[T, Any]:
        return await self._execute_with_response(self._values(request), options)

    async def pages(
        self,
        *args: Any,
        max_pages: int | None = None,
        options: CallOptions | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[T]:
        """Successive pages, from the request the arguments describe, through ``__pages__``.

        Every page is one ordinary ``send()``; the strategy only decides the next request.
        """

        strategy = self._strategy(max_pages)
        request = self.request(*args, **kwargs)
        sent = 0
        while max_pages is None or sent < max_pages:
            result = await self.send(request, options=options)
            sent += 1
            yield result
            changes = next_changes(strategy, request, result, fresh=len(strategy.items(result)))
            if changes is None:
                return
            request = self.evolve(request, **changes)

    async def items(
        self,
        *args: Any,
        max_pages: int | None = None,
        options: CallOptions | None = None,
        key: Callable[[Any], Hashable] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """The elements of successive pages, flattened; ``key=`` skips repeats across pages.

        A page that contributes nothing new ends the iteration, which also bounds a server that
        answers the last page again for every number past it.
        """

        strategy = self._strategy(max_pages)
        request = self.request(*args, **kwargs)
        seen: set[Hashable] = set()
        sent = 0
        while max_pages is None or sent < max_pages:
            result = await self.send(request, options=options)
            sent += 1
            fresh = 0
            for item in strategy.items(result):
                if key is not None:
                    mark = key(item)
                    if mark in seen:
                        continue
                    seen.add(mark)
                fresh += 1
                yield item
            changes = next_changes(strategy, request, result, fresh=fresh)
            if changes is None:
                return
            request = self.evolve(request, **changes)

    async def _execute(self, values: dict[str, object], options: CallOptions | None) -> T:
        result = await self._api._client._execute_operation(
            self._descriptor.resolve_for(self._api),
            values,
            options=options,
            with_response=False,
            identity=self._api._scope,
            serialization=self._api._serialization,
        )
        return cast(T, result)

    async def _execute_with_response(
        self, values: dict[str, object], options: CallOptions | None
    ) -> ResponseEnvelope[T, Any]:
        result = await self._api._client._execute_operation(
            self._descriptor.resolve_for(self._api),
            values,
            options=options,
            with_response=True,
            identity=self._api._scope,
            serialization=self._api._serialization,
        )
        return cast(ResponseEnvelope[T, Any], result)


class _BoundSyncOperation[**P, T](_BoundOperation[P, T]):
    _api: SyncApi

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        values, options = self._bind(*args, **kwargs)
        return self._execute(values, options)

    def with_response(
        self,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> ResponseEnvelope[T, Any]:
        values, options = self._bind(*args, **kwargs)
        return self._execute_with_response(values, options)

    def prepare(
        self,
        *args: Any,
        options: PrepareOptions | None = None,
        **kwargs: Any,
    ) -> PreparedCall:
        values, _ = self._bind(*args, **kwargs)
        return self._api._client._prepare_operation(
            self._descriptor.resolve_for(self._api),
            values,
            options=options or PrepareOptions(),
            identity=self._api._scope,
            serialization=self._api._serialization,
        )

    def send(self, request: HttpOperation[T], /, *, options: CallOptions | None = None) -> T:
        """Send a request value built by :meth:`request` or :meth:`evolve`."""

        return self._execute(self._values(request), options)

    def send_with_response(
        self, request: HttpOperation[T], /, *, options: CallOptions | None = None
    ) -> ResponseEnvelope[T, Any]:
        return self._execute_with_response(self._values(request), options)

    def pages(
        self,
        *args: Any,
        max_pages: int | None = None,
        options: CallOptions | None = None,
        **kwargs: Any,
    ) -> Iterator[T]:
        """Successive pages, from the request the arguments describe, through ``__pages__``."""

        strategy = self._strategy(max_pages)
        request = self.request(*args, **kwargs)
        sent = 0
        while max_pages is None or sent < max_pages:
            result = self.send(request, options=options)
            sent += 1
            yield result
            changes = next_changes(strategy, request, result, fresh=len(strategy.items(result)))
            if changes is None:
                return
            request = self.evolve(request, **changes)

    def items(
        self,
        *args: Any,
        max_pages: int | None = None,
        options: CallOptions | None = None,
        key: Callable[[Any], Hashable] | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """The elements of successive pages, flattened; ``key=`` skips repeats across pages."""

        strategy = self._strategy(max_pages)
        request = self.request(*args, **kwargs)
        seen: set[Hashable] = set()
        sent = 0
        while max_pages is None or sent < max_pages:
            result = self.send(request, options=options)
            sent += 1
            fresh = 0
            for item in strategy.items(result):
                if key is not None:
                    mark = key(item)
                    if mark in seen:
                        continue
                    seen.add(mark)
                fresh += 1
                yield item
            changes = next_changes(strategy, request, result, fresh=fresh)
            if changes is None:
                return
            request = self.evolve(request, **changes)

    def _execute(self, values: dict[str, object], options: CallOptions | None) -> T:
        result = self._api._client._execute_operation(
            self._descriptor.resolve_for(self._api),
            values,
            options=options,
            with_response=False,
            identity=self._api._scope,
            serialization=self._api._serialization,
        )
        return cast(T, result)

    def _execute_with_response(
        self, values: dict[str, object], options: CallOptions | None
    ) -> ResponseEnvelope[T, Any]:
        result = self._api._client._execute_operation(
            self._descriptor.resolve_for(self._api),
            values,
            options=options,
            with_response=True,
            identity=self._api._scope,
            serialization=self._api._serialization,
        )
        return cast(ResponseEnvelope[T, Any], result)


class _OperationDescriptor[**P, T]:
    """One operation class on a router: immutable declaration, bound per router instance.

    ``__set_name__`` records the owner, the attribute name and, for ``op()``, whether the
    owner is asynchronous; the resolved declaration is cached on the router instance, never
    here. ``accepts_options`` and ``signature`` exist only so a decorated operation keeps its
    ``options=`` parameter and its function signature in the IDE; nothing in the compiler or
    the executor reads them.
    """

    def __init__(
        self,
        operation_type: type[HttpOperation[T]],
        spec: _HttpSpec,
        *,
        accepts_options: bool = False,
        signature: inspect.Signature | None = None,
        synthesized: bool = False,
        pages: Pagination[T] | None = None,
    ) -> None:
        self.operation_type = operation_type
        self.spec = spec
        self.pages = pages
        self.accepts_options = accepts_options
        self.signature = signature
        self.synthesized = synthesized
        self.owner: type[object] | None = None
        self.name = ""
        self.asynchronous: bool | None = None
        self._declared: _OperationDeclaration[T] | None = None
        self.__name__ = operation_type.__name__
        self.__qualname__ = operation_type.__qualname__
        self.__doc__ = operation_type.__doc__
        if signature is not None:
            self.__signature__ = signature

    # -- declaration -----------------------------------------------------------------

    @property
    def Operation(self) -> type[HttpOperation[T]]:
        return self.operation_type

    @property
    def operation_id(self) -> str:
        if self.spec.operation_id is not None:
            return self.spec.operation_id
        return self.operation_type.__qualname__

    def __set_name__(self, owner: type[object], name: str) -> None:
        self.owner = owner
        self.name = name
        if self.synthesized:
            self.operation_type.__qualname__ = f"{owner.__qualname__}.{name}.Operation"
            self.__qualname__ = f"{owner.__qualname__}.{name}"
        elif issubclass(owner, AsyncApi):
            self.asynchronous = True
        elif issubclass(owner, SyncApi):
            self.asynchronous = False

    def declare(
        self,
        models: ModelAdapterRegistry,
        *,
        unwrap: str | None = None,
    ) -> _OperationDeclaration[T]:
        """The unresolved declaration read from the class with one model registry."""

        spec = self.spec
        operation_id = self.operation_id
        generic = generic_argument(self.operation_type, HttpOperation)
        result_type = result_type_of(spec.success, generic)
        if result_type is None:
            raise PlanError(
                f"operation class {self.operation_type.__name__} declares neither "
                "HttpOperation[T] nor success="
            )
        input_schema = inspect_operation_input(
            self.operation_type,
            operation_id=operation_id,
            path=spec.path,
            models=models,
            projection=spec.projection,
        )
        if spec.envelope_cases:
            _validate_envelope_placements(input_schema, operation_id)
        if spec.envelope_cases and spec.success is None:
            # The short form: the result type is the payload, and ``errors`` is keyed by the
            # protocol's codes. An operation that names its own cases keeps them.
            success_cases, error_cases_, fallback_case = rpc_responses(
                result_type=result_type,
                errors=spec.errors,
                fallback=spec.fallback,
                operation_id=operation_id,
            )
            responses = Responses(
                success=success_cases, errors=error_cases_, fallback=fallback_case
            )
        else:
            responses = normalize_responses(
                result_type=result_type,
                success=spec.success,
                errors=spec.errors,
                fallback=spec.fallback,
                models=models,
                unwrap=unwrap,
                operation_id=operation_id,
            )
        scope = RequestScope(
            path_prefixes=(spec.path,),
            methods=frozenset({spec.method}),
            operation_ids=frozenset({operation_id}),
        )
        return _OperationDeclaration(
            operation_id=operation_id,
            method=spec.method,
            path=spec.path,
            input_fields=input_schema.fields,
            input_schema=input_schema,
            result_type=result_type,
            responses=responses,
            operation_type=self.operation_type,
            projection=spec.projection,
            requires=spec.requires,
            inject=spec.inject,
            protections=spec.protections,
            wire=spec.wire,
            scope=scope,
            tags=spec.tags,
            idempotent=spec.idempotent,
            raw_response=spec.raw_response,
            discriminator=spec.discriminator,
        )

    @property
    def declaration(self) -> _OperationDeclaration[T]:
        """The declaration as read with the default model adapters, computed once."""

        if self._declared is None:
            self._declared = self.declare(default_model_adapters())
        return self._declared

    def resolve(
        self,
        defaults: _ServiceDefaults = _NO_DEFAULTS,
        models: ModelAdapterRegistry | None = None,
    ) -> _OperationDeclaration[T]:
        """The declaration merged with its service: errors chain, security, signing, wire."""

        registry = default_model_adapters() if models is None else models
        if registry is default_model_adapters() and defaults.unwrap is None:
            declaration = self.declaration
        else:
            declaration = self.declare(registry, unwrap=defaults.unwrap)
        spec = self.spec
        declared_security = spec.security
        security = (
            defaults.security
            if isinstance(declared_security, _Inherit)
            else declared_security
        )
        declared_signing = spec.signing
        signing: tuple[RequestSignature, ...]
        if isinstance(declared_signing, _Inherit):
            signing = defaults.signing
        elif declared_signing is None:
            signing = ()
        elif isinstance(declared_signing, tuple):
            signing = declared_signing
        else:
            signing = (declared_signing,)
        crypto = defaults.crypto if spec.crypto is _INHERIT else spec.crypto
        responses = cast(Responses[T], declaration.responses)
        if spec.inherit_errors and defaults.errors:
            service_errors = tuple(
                replace(case, precedence=1)
                for item in defaults.errors
                for case in error_cases(
                    item if isinstance(item, Mapping) else (item,),
                    models=registry,
                    operation_id=declaration.operation_id,
                )
            )
            responses = Responses(
                success=cast(tuple[Success[T], ...], responses.success),
                errors=(*service_errors, *responses.errors),
                fallback=responses.fallback,
            )
        _validate_allowed(defaults.allow, declaration.operation_id, security, signing)
        if defaults.signed and not signing:
            raise TypeError(
                f"operation {declaration.operation_id!r} carries no signature, and its "
                "service requires every operation to be signed"
            )
        envelope = defaults.protocol
        addressing: dict[str, object] = {}
        if declaration.discriminator is not None:
            if envelope is None:
                raise TypeError(
                    f"operation {declaration.operation_id!r} is declared with @api.rpc, "
                    "and its service declares no protocol envelope"
                )
            addressing = {
                "path": getattr(envelope, "path", "/"),
                "method": getattr(envelope, "method", "POST"),
                "envelope": envelope,
            }
        return replace(
            declaration,
            **cast(Any, addressing),
            base_url=defaults.base_url,
            responses=responses,
            security=security,
            signing=signing,
            crypto=cast(PayloadCrypto | None, crypto),
            wire=declaration.wire.over(defaults.wire),
            crypto_inherit=spec.crypto is _INHERIT and defaults.crypto is None,
        )

    def resolve_for(self, api: _ApiBase) -> _OperationDeclaration[T]:
        """Resolved declaration for one bound router, merged once per router instance."""

        cached = api._resolved.get(self)
        if cached is None:
            cached = self.resolve(api._defaults, api._serialization.models)
            api._resolved[self] = cached
        return cast("_OperationDeclaration[T]", cached)

    # -- binding ---------------------------------------------------------------------

    def bound_signature(self) -> inspect.Signature:
        if self.signature is not None:
            return _bound_signature(self.signature)
        return inspect.signature(self.operation_type)

    def check_request(self, request: object) -> None:
        if not isinstance(request, self.operation_type):
            raise TypeError(
                f"send() expects {self.operation_type.__name__}, got {type(request).__name__}"
            )

    def _bind_arguments(
        self,
        api: _ApiBase,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, object], CallOptions | None]:
        options: object = None
        if self.accepts_options:
            options = kwargs.pop("options", None)
            if options is not None and not isinstance(options, CallOptions):
                raise TypeError("options must be CallOptions or None")
        request = self.operation_type(*args, **kwargs)
        declaration = self.resolve_for(api)
        return self._values_of(request, declaration), cast(CallOptions | None, options)

    def _values_of(
        self, request: object, declaration: _OperationDeclaration[T]
    ) -> dict[str, object]:
        """The field values the executor takes; ``UNSET`` is what "not passed" looks like."""

        return {
            field.python_name: value
            for field in declaration.input_fields
            if not isinstance(value := getattr(request, field.python_name), Unset)
        }

    # -- descriptor protocol ---------------------------------------------------------

    @overload
    def __get__(self, instance: None, owner: type[object]) -> _OperationDescriptor[P, T]: ...

    @overload
    def __get__(
        self, instance: AsyncApi, owner: type[object] | None = None
    ) -> _BoundAsyncOperation[P, T]: ...

    @overload
    def __get__(
        self, instance: SyncApi, owner: type[object] | None = None
    ) -> _BoundSyncOperation[P, T]: ...

    def __get__(self, instance: object | None, owner: type[object] | None = None) -> object:
        if instance is None:
            return self
        if isinstance(instance, AsyncApi):
            return _BoundAsyncOperation(self, instance)
        if isinstance(instance, SyncApi):
            return _BoundSyncOperation(self, instance)
        raise TypeError("an operation is bound through a SyncApi or AsyncApi router")


def _validate_envelope_placements(schema: MethodInputSchema, operation_id: str) -> None:
    """D-20: an RPC operation has no URL of its own, so it cannot place a field in one."""

    for field in schema.fields:
        if field.location in (RequestLocation.PATH, RequestLocation.QUERY):
            place = "path" if field.location is RequestLocation.PATH else "query"
            raise PlanError(
                f"RPC operation {operation_id} cannot place {field.python_name!r} in {place}; "
                "the envelope owns the URL"
            )


@runtime_checkable
class _PublishesItself[TDescriptor](Protocol):
    """A non-HTTP operation class publishes its own router member.

    This is the whole of what the HTTP side knows about the other protocols: ask the class
    what it becomes on a router, and it answers with its own descriptor.
    """

    @classmethod
    def __publish__(cls) -> TDescriptor: ...


@overload
def op[TDescriptor](operation: type[_PublishesItself[TDescriptor]], /) -> TDescriptor: ...


@overload
def op[**P, T](operation: Callable[P, HttpOperation[T]], /) -> _OperationDescriptor[P, T]: ...


def op(operation: Any, /) -> Any:
    """Publish an operation class on a router with the constructor's own signature."""

    if not isinstance(operation, type):
        raise TypeError(f"op() expects an operation class, got {operation!r}")
    if isinstance(operation, _PublishesItself):
        # A protocol that is not HTTP publishes itself: the HTTP side never learns its
        # package exists, which is the layering the WS boundary test enforces.
        return operation.__publish__()
    if not issubclass(operation, HttpOperation):
        raise TypeError(
            "op() expects a subclass of HttpOperation, RpcOperation, WsCall, WsSubscribe "
            "or WsSend"
        )
    spec = getattr(operation, "__http__", None)
    if not isinstance(spec, _HttpSpec):
        raise PlanError(
            f"operation class {operation.__name__} has no __http__; assign Http.get(...) "
            "or another verb"
        )
    return _OperationDescriptor(operation, spec, pages=_pages_of(operation, spec))


def _pages_of(operation: type[HttpOperation[Any]], spec: _HttpSpec) -> Pagination[Any] | None:
    """The validated ``__pages__`` of an operation class, checked when ``op()`` runs."""

    strategy = getattr(operation, "__pages__", None)
    if strategy is None:
        return None
    names: list[str] = []
    for cls in reversed(operation.__mro__):
        for name in getattr(cls, "__annotations__", {}):
            if not name.startswith("_") and name not in names:
                names.append(name)
    result_type = result_type_of(spec.success, generic_argument(operation, HttpOperation))
    return validate_declaration(
        strategy, operation_type=operation, field_names=names, result_type=result_type
    )


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
        and not isinstance(base.__dict__[name], _ApiGroup | _OperationDescriptor)
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
    defaults = _ServiceDefaults(**values)
    if defaults.protocol is not None and defaults.unwrap is not None:
        raise TypeError(
            f"service {cls.__name__} declares both protocol and unwrap; the envelope "
            "already selects the payload"
        )
    return defaults


def _normalize_service_attribute(cls: type[object], name: str, value: object) -> object:
    if name == "base_url":
        if not isinstance(value, str):
            raise TypeError(f"{cls.__name__}.base_url must be a string")
        return validate_base_url(value, f"{cls.__name__}.base_url")
    if name == "signed":
        if not isinstance(value, bool):
            raise TypeError(f"{cls.__name__}.signed must be a boolean")
        return value
    if name == "unwrap":
        if not isinstance(value, str) or not value.startswith("/"):
            raise TypeError(f"{cls.__name__}.unwrap must be a JSON pointer such as '/data'")
        return value
    if name == "errors":
        if isinstance(value, Mapping):
            return (value,)
        return value if isinstance(value, tuple) else (value,)
    if name in {"signing", "allow"}:
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


class _OperationDecorator:
    """``@api.get(...)``: synthesize the operation class from a function, then ``op()`` it.

    The function body is never called. Its keyword-only parameters become the fields of a
    frozen, slotted dataclass whose ``__http__`` is the spec the verb built; a ``None``
    default becomes ``Omittable[...] = UNSET`` so "not passed" keeps meaning "not sent".
    """

    def __init__(self, spec: _HttpSpec) -> None:
        self.spec = spec

    @overload
    def __call__(
        self,
        declaration: Callable[Concatenate[TAsyncApi, P], Coroutine[Any, Any, TResult]],
    ) -> _OperationDescriptor[P, TResult]: ...

    @overload
    def __call__(
        self,
        declaration: Callable[Concatenate[TSyncApi, P], TResult],
    ) -> _OperationDescriptor[P, TResult]: ...

    def __call__(self, declaration: Callable[..., Any]) -> object:
        signature = inspect.signature(declaration)
        parameters = tuple(signature.parameters.values())
        if not parameters:
            raise TypeError("an API operation must be an instance method")
        self_parameter = parameters[0].name
        operation_id = self.spec.operation_id or declaration.__name__
        hints = get_type_hints(declaration, include_extras=True)
        result_type = hints.get("return")
        if result_type is None:
            result_type = result_type_of(self.spec.success, None)
        if result_type is None:
            raise TypeError(
                f"operation {operation_id!r} cannot infer its result type from responses; "
                "add a return annotation or declare at least one unambiguous success response"
            )
        _validate_options(signature, hints, self_parameter, operation_id)
        fields = _synthesized_fields(parameters[1:], hints, operation_id=operation_id)
        spec = replace(self.spec, operation_id=operation_id)
        base: Any = RpcOperation if spec.envelope_cases else HttpOperation
        operation_type = dataclasses.make_dataclass(
            declaration.__name__,
            fields,
            bases=(base[result_type],),
            frozen=True,
            slots=True,
            kw_only=True,
            namespace={
                "__http__": spec,
                "__module__": declaration.__module__,
                "__doc__": declaration.__doc__,
            },
        )
        operation_type.__qualname__ = f"{declaration.__qualname__}.Operation"
        public_signature = signature.replace(return_annotation=result_type)
        descriptor: _OperationDescriptor[Any, Any] = _OperationDescriptor(
            cast(type[HttpOperation[Any]], operation_type),
            spec,
            accepts_options=True,
            signature=public_signature,
            synthesized=True,
        )
        descriptor.__doc__ = declaration.__doc__
        descriptor.asynchronous = inspect.iscoroutinefunction(declaration)
        # A decorated operation reports its declaration errors where it is written.
        descriptor.declaration  # noqa: B018 - evaluated for its diagnostics
        return descriptor


def _synthesized_fields(
    parameters: tuple[inspect.Parameter, ...],
    hints: Mapping[str, object],
    *,
    operation_id: str,
) -> list[tuple[str, object] | tuple[str, object, Any]]:
    fields: list[tuple[str, object] | tuple[str, object, Any]] = []
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise PlanError(f"operation {operation_id!r} cannot declare variadic parameters")
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            raise PlanError(
                f"operation {operation_id!r} uses **{parameter.name}: Unpack[...], which "
                "0.3.0 removed; declare an operation class and publish it with op(...)"
            )
        if parameter.name == "options":
            continue
        if parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            raise PlanError(
                f"request parameter {parameter.name!r} in {operation_id!r} must be keyword-only"
            )
        hint = hints.get(parameter.name)
        if hint is None:
            raise PlanError(
                f"input field {parameter.name!r} in {operation_id!r} requires an annotation"
            )
        default = parameter.default
        if default is inspect.Parameter.empty:
            fields.append((parameter.name, hint))
        elif default is None:
            omittable: object = Omittable[hint]  # type: ignore[valid-type]
            fields.append((parameter.name, omittable, dataclasses.field(default=UNSET)))
        elif getattr(type(default), "__hash__", None) is None:
            fields.append(
                (parameter.name, hint, dataclasses.field(default_factory=_constant(default)))
            )
        else:
            fields.append((parameter.name, hint, dataclasses.field(default=default)))
    return fields


def _constant(value: object) -> Callable[[], object]:
    return lambda: value


class _Verb:
    def __init__(self, method: str) -> None:
        self.method = method.upper()

    def __call__(self, path: str, /, **options: Unpack[_HttpOptions]) -> _OperationDecorator:
        return _OperationDecorator(Http.request(self.method, path, **options))


def _validate_options(
    signature: inspect.Signature,
    hints: Mapping[str, object],
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
    operation_ids: dict[str, str] = {}
    envelope = _service_defaults_of(cls).protocol
    for name in dir(cls):
        descriptor = inspect.getattr_static(cls, name)
        if not isinstance(descriptor, _OperationDescriptor):
            continue
        if descriptor.spec.discriminator is not None and envelope is None:
            raise TypeError(
                f"operation {descriptor.operation_id!r} is declared with @api.rpc, "
                "and its service declares no protocol envelope"
            )
        if descriptor.asynchronous is not None and descriptor.asynchronous != asynchronous:
            kind = "async" if asynchronous else "sync"
            raise TypeError(f"{kind} API operation {name!r} has the wrong function kind")
        operation_id = descriptor.operation_id
        class_name = descriptor.operation_type.__qualname__
        if operation_id in operation_ids:
            raise TypeError(
                f"duplicate operation_id: {operation_id} "
                f"(declared by {operation_ids[operation_id]} and {class_name})"
            )
        operation_ids[operation_id] = class_name


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

    def rpc(self, discriminator: str, /, **options: Unpack[_HttpOptions]) -> _OperationDecorator:
        """One operation of a service whose method name travels in the body, not the path.

        Not a second family of decorators: every other argument is the one ``api.post`` takes
        and means the same thing. The URL is not repeated here because it belongs to the
        service's envelope, declared once as the router's ``protocol`` attribute.
        """

        return _OperationDecorator(Rpc.method(discriminator, **options))

    def request(
        self, method: str, path: str, /, **options: Unpack[_HttpOptions]
    ) -> _OperationDecorator:
        return _OperationDecorator(Http.request(method, path, **options))


api = _ApiNamespace()


__all__ = [
    "AsyncApi",
    "SyncApi",
    "api",
    "api_group",
    "op",
]
