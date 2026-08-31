"""Scoped call/attempt middleware for the single compiled executor."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from eazy_sdk._internal import BoundArguments, OperationIdentity, RequestScope, ValuePatch
from eazy_sdk.request.prepared import PreparedRequest
from eazy_sdk.response import NormalizedResponse, ResponseContext


class MiddlewareProtocolError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CallMiddlewareContext[T]:
    operation: OperationIdentity
    arguments: BoundArguments
    metadata: object | None = None

    def with_arguments(self, arguments: BoundArguments) -> CallMiddlewareContext[T]:
        return replace(self, arguments=arguments)


type CallOutcome[T] = T
type NextCall[T] = Callable[[CallMiddlewareContext[T]], Awaitable[CallOutcome[T]]]


class CallMiddleware(Protocol):
    def __call__[T](
        self, context: CallMiddlewareContext[T], call_next: NextCall[T]
    ) -> CallOutcome[T] | Awaitable[CallOutcome[T]]: ...


@dataclass(frozen=True, slots=True)
class AttemptRequestContext:
    operation: OperationIdentity
    attempt: int


@dataclass(frozen=True, slots=True)
class PreparedAttemptContext:
    operation: OperationIdentity
    attempt: int
    request: PreparedRequest


@dataclass(frozen=True, slots=True)
class AttemptResponseContext:
    operation: OperationIdentity
    attempt: int
    response: ResponseContext[object]


@dataclass(frozen=True, slots=True)
class AttemptTransportErrorContext:
    operation: OperationIdentity
    attempt: int
    error: Exception


@dataclass(frozen=True, slots=True)
class Continue:
    pass


@dataclass(frozen=True, slots=True)
class ReplaceResponse:
    response: NormalizedResponse[Any]


@dataclass(frozen=True, slots=True)
class ProposeAction:
    action: object


@dataclass(frozen=True, slots=True)
class Fail:
    error: Exception


@dataclass(frozen=True, slots=True)
class RetryAttempt:
    patch: ValuePatch = field(default_factory=lambda: ValuePatch(()))
    kind: str = "middleware-replay"


@dataclass(frozen=True, slots=True)
class RedirectTo:
    url: str


type ResponseDecision = Continue | ReplaceResponse | ProposeAction | Fail
type TransportDecision = Continue | ProposeAction | Fail
type EmitDecision = Continue | Fail


class AttemptMiddleware(Protocol):
    def contribute(
        self, context: AttemptRequestContext
    ) -> ValuePatch | None | Awaitable[ValuePatch | None]: ...

    def before_emit(self, context: PreparedAttemptContext) -> EmitDecision | None: ...

    def after_response(
        self, context: AttemptResponseContext
    ) -> ResponseDecision | None | Awaitable[ResponseDecision | None]: ...

    def on_transport_error(
        self, context: AttemptTransportErrorContext
    ) -> TransportDecision | None | Awaitable[TransportDecision | None]: ...


@dataclass(frozen=True, slots=True)
class CallMiddlewareRegistration:
    implementation: CallMiddleware
    scope: RequestScope = field(default_factory=RequestScope)


@dataclass(frozen=True, slots=True)
class AttemptMiddlewareRegistration:
    implementation: object
    scope: RequestScope = field(default_factory=RequestScope)
    reads: tuple[object, ...] = ()
    writes: tuple[object, ...] = ()


type MiddlewareRegistration = CallMiddlewareRegistration | AttemptMiddlewareRegistration


def call_middleware(
    implementation: CallMiddleware, *, scope: RequestScope | None = None
) -> CallMiddlewareRegistration:
    return CallMiddlewareRegistration(implementation, scope or RequestScope())


def attempt_middleware(
    implementation: object,
    *,
    scope: RequestScope | None = None,
    reads: tuple[object, ...] = (),
    writes: tuple[object, ...] = (),
) -> AttemptMiddlewareRegistration:
    return AttemptMiddlewareRegistration(implementation, scope or RequestScope(), reads, writes)


class SingleUseNext[T]:
    def __init__(self, callback: NextCall[T]) -> None:
        self._callback = callback
        self._used = False

    async def __call__(self, context: CallMiddlewareContext[T]) -> CallOutcome[T]:
        if self._used:
            raise MiddlewareProtocolError("call_next is a single-use capability")
        self._used = True
        return await self._callback(context)
