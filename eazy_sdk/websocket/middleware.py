"""Scoped WebSocket middleware contracts without transport or lifecycle capabilities."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from enum import Enum
from typing import Protocol

from ._artifacts import PreparedMessage, freeze_value, thaw_value


class WsDirection(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


@dataclass(frozen=True, slots=True)
class WsMiddlewareContext:
    operation: str | None
    endpoint: str
    protocol: str
    channel: str | None
    event: str | None
    direction: WsDirection | None
    generation: int


@dataclass(frozen=True, slots=True)
class WsScope:
    operations: frozenset[str] = frozenset()
    endpoints: frozenset[str] = frozenset()
    protocols: frozenset[str] = frozenset()
    channels: frozenset[str] = frozenset()
    events: frozenset[str] = frozenset()
    directions: frozenset[WsDirection] = frozenset()

    def matches(self, context: WsMiddlewareContext) -> bool:
        return (
            _matches(self.operations, context.operation)
            and _matches(self.endpoints, context.endpoint)
            and _matches(self.protocols, context.protocol)
            and _matches(self.channels, context.channel)
            and _matches(self.events, context.event)
            and (not self.directions or context.direction in self.directions)
        )


def _matches(values: frozenset[str], candidate: str | None) -> bool:
    return not values or candidate in values


@dataclass(frozen=True, slots=True)
class WsContinue:
    pass


@dataclass(frozen=True, slots=True)
class WsReject:
    error: Exception


@dataclass(frozen=True, slots=True)
class WsOutput:
    path: tuple[str, ...]
    value: object

    def __post_init__(self) -> None:
        if not self.path or not all(self.path):
            raise ValueError("middleware output path is invalid")


@dataclass(frozen=True, slots=True)
class WsMessagePatch:
    outputs: tuple[WsOutput, ...]


type WsMiddlewareResult = WsContinue | WsReject | WsMessagePatch
type MiddlewareCallable = Callable[
    [WsMiddlewareContext],
    WsMiddlewareResult | Awaitable[WsMiddlewareResult],
]


class ConnectionMiddleware(Protocol):
    def __call__(
        self,
        context: WsMiddlewareContext,
    ) -> WsContinue | WsReject | Awaitable[WsContinue | WsReject]: ...


class MessageMiddleware(Protocol):
    def __call__(
        self,
        context: WsMiddlewareContext,
    ) -> WsMiddlewareResult | Awaitable[WsMiddlewareResult]: ...


class SubscriptionMiddleware(Protocol):
    def __call__(
        self,
        context: WsMiddlewareContext,
    ) -> WsContinue | WsReject | Awaitable[WsContinue | WsReject]: ...


@dataclass(frozen=True, slots=True)
class ConnectionMiddlewareApplication:
    implementation: ConnectionMiddleware = dataclass_field(repr=False)
    scope: WsScope = WsScope()


@dataclass(frozen=True, slots=True)
class MessageMiddlewareApplication:
    implementation: MessageMiddleware = dataclass_field(repr=False)
    scope: WsScope = WsScope()


@dataclass(frozen=True, slots=True)
class SubscriptionMiddlewareApplication:
    implementation: SubscriptionMiddleware = dataclass_field(repr=False)
    scope: WsScope = WsScope()


async def evaluate_middleware(
    applications: Sequence[
        ConnectionMiddlewareApplication
        | MessageMiddlewareApplication
        | SubscriptionMiddlewareApplication,
    ],
    context: WsMiddlewareContext,
    *,
    allow_patch: bool,
) -> tuple[WsOutput, ...]:
    outputs: list[WsOutput] = []
    paths: set[tuple[str, ...]] = set()
    for application in applications:
        if not application.scope.matches(context):
            continue
        result = application.implementation(context)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, WsReject):
            raise result.error
        if isinstance(result, WsContinue):
            continue
        if not isinstance(result, WsMessagePatch):
            raise TypeError("WebSocket middleware returned an invalid typed decision")
        if not allow_patch:
            raise TypeError("this WebSocket middleware lifetime cannot patch messages")
        for output in result.outputs:
            if output.path in paths:
                raise ValueError(f"duplicate middleware output path: {'.'.join(output.path)}")
            paths.add(output.path)
            outputs.append(output)
    return tuple(outputs)


def apply_message_patch(
    message: PreparedMessage,
    outputs: tuple[WsOutput, ...],
) -> PreparedMessage:
    raw = thaw_value(message.envelope)
    if not isinstance(raw, dict):
        raise TypeError("message middleware requires an object protocol envelope")
    for output in outputs:
        target = raw
        for component in output.path[:-1]:
            child = target.setdefault(component, {})
            if not isinstance(child, dict):
                raise TypeError(f"middleware output path collides at {component!r}")
            target = child
        target[output.path[-1]] = output.value
    return replace(message, envelope=freeze_value(raw))


__all__ = [
    "ConnectionMiddleware",
    "ConnectionMiddlewareApplication",
    "MessageMiddleware",
    "MessageMiddlewareApplication",
    "SubscriptionMiddleware",
    "SubscriptionMiddlewareApplication",
    "WsContinue",
    "WsDirection",
    "WsMessagePatch",
    "WsMiddlewareContext",
    "WsOutput",
    "WsReject",
    "WsScope",
]
