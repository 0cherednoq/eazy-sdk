"""Explicit wire codecs kept separate from model-library conversion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Literal, Protocol, runtime_checkable

type ScalarLocation = Literal["path", "query", "header", "cookie", "form"]


@dataclass(frozen=True, slots=True)
class ScalarEncodeContext:
    location: ScalarLocation
    wire_name: str
    operation_id: str


@runtime_checkable
class ScalarCodec(Protocol):
    @property
    def name(self) -> str: ...

    def encode(self, value: object, context: ScalarEncodeContext) -> str: ...


@dataclass(frozen=True, slots=True)
class DefaultScalarCodec:
    name: str = "default"

    def encode(self, value: object, context: ScalarEncodeContext) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, Enum):
            return self.encode(value.value, context)
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, str | int | float):
            return str(value)
        raise TypeError(
            f"scalar codec {self.name!r} cannot encode {type(value).__name__} "
            f"for {context.location} field {context.wire_name!r}"
        )


@dataclass(frozen=True, slots=True)
class DelimitedScalarCodec:
    separator: str = ","
    name: str = "delimited"

    def encode(self, value: object, context: ScalarEncodeContext) -> str:
        if not isinstance(value, (list, tuple)):
            raise TypeError(
                f"scalar codec {self.name!r} requires a sequence for "
                f"{context.location} field {context.wire_name!r}"
            )
        primitive = DefaultScalarCodec()
        return self.separator.join(primitive.encode(item, context) for item in value)


@dataclass(frozen=True, slots=True)
class EncodeContext:
    operation_id: str


@runtime_checkable
class BodyCodec(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def media_type(self) -> str | None: ...

    def encode(self, value: object, context: EncodeContext) -> bytes: ...


__all__ = [
    "BodyCodec",
    "DefaultScalarCodec",
    "DelimitedScalarCodec",
    "EncodeContext",
    "ScalarCodec",
    "ScalarEncodeContext",
    "ScalarLocation",
]
