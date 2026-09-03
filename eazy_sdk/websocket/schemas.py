"""WebSocket payload schemas and typed inbound case arbitration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from eazy_sdk.core.kernel import (
    AmbiguousCases,
    CaseArbitration,
    Malformed,
    MalformedCase,
    NoCaseMatch,
    SelectedCase,
    arbitrate_cases,
)
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters

from ._artifacts import FrozenValue, freeze_value, thaw_value
from .errors import (
    AmbiguousMessageError,
    MalformedMessageError,
    RemoteMessageError,
    UnexpectedMessageError,
)
from .protocols import ProtocolMessage


@dataclass(frozen=True, slots=True)
class JsonPayload[T = object]:
    model: type[T] | object = object

    def prepare(self, value: object, models: ModelAdapterRegistry) -> FrozenValue:
        if self.model is object:
            normalized = models.dump(value)
        else:
            model = cast(type[object], self.model)
            loaded = models.load(model, value)
            normalized = models.dump(loaded)
        return freeze_value(normalized)


@dataclass(frozen=True, slots=True)
class EmptyPayload:
    def prepare(self, value: object, models: ModelAdapterRegistry) -> FrozenValue:
        if value not in ({}, None):
            raise TypeError("empty WebSocket payload does not accept values")
        return freeze_value({})


type OutboundPayload = JsonPayload[Any] | EmptyPayload


@dataclass(frozen=True, slots=True, eq=False)
class SuccessReply[T]:
    discriminator: str
    model: type[T]


@dataclass(frozen=True, slots=True, eq=False)
class ErrorReply[T]:
    discriminator: str
    model: type[T]


@dataclass(frozen=True, slots=True, eq=False)
class Message[T]:
    discriminator: str
    model: type[T]


type ReplyCase = SuccessReply[Any] | ErrorReply[Any]


@dataclass(frozen=True, slots=True)
class Replies:
    success: tuple[SuccessReply[Any], ...]
    errors: tuple[ErrorReply[Any], ...] = ()
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters, repr=False)

    @property
    def cases(self) -> tuple[ReplyCase, ...]:
        return (*self.success, *self.errors)

    def inspect(self, message: ProtocolMessage) -> CaseArbitration[ReplyCase, object]:
        return _inspect_cases(self.cases, message, self.models)


@dataclass(frozen=True, slots=True)
class Messages:
    cases: tuple[Message[Any], ...]
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters, repr=False)

    def inspect(self, message: ProtocolMessage) -> CaseArbitration[Message[Any], object]:
        return _inspect_cases(self.cases, message, self.models)


def _inspect_cases[TCase: SuccessReply[Any] | ErrorReply[Any] | Message[Any]](
    cases: tuple[TCase, ...],
    message: ProtocolMessage,
    models: ModelAdapterRegistry,
) -> CaseArbitration[TCase, object]:
    candidates = tuple(case for case in cases if case.discriminator == message.discriminator)
    if not candidates:
        return NoCaseMatch()
    raw = thaw_value(message.payload)
    matches: list[tuple[TCase, object]] = []
    malformed: list[tuple[TCase, Malformed]] = []
    for case in candidates:
        try:
            matches.append((case, models.load(case.model, raw)))
        except Exception as exc:
            malformed.append((case, Malformed(exc)))
    return arbitrate_cases(matches, malformed)


def selected_value[TCase: ReplyCase | Message[Any]](
    arbitration: CaseArbitration[TCase, object],
) -> object:
    if isinstance(arbitration, SelectedCase):
        if isinstance(arbitration.case, ErrorReply):
            raise RemoteMessageError(
                arbitration.value,
                discriminator=arbitration.case.discriminator,
            )
        return arbitration.value
    if isinstance(arbitration, AmbiguousCases):
        raise AmbiguousMessageError("multiple WebSocket message cases matched")
    if isinstance(arbitration, MalformedCase):
        raise MalformedMessageError("recognized WebSocket payload is malformed") from (
            arbitration.malformed.cause
        )
    raise UnexpectedMessageError("no WebSocket message case matched")


__all__ = [
    "EmptyPayload",
    "ErrorReply",
    "JsonPayload",
    "Message",
    "Messages",
    "OutboundPayload",
    "Replies",
    "ReplyCase",
    "SuccessReply",
]
