"""Logical body inputs consumed by the Zapros client boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class NoBodyInput:
    pass


@dataclass(frozen=True, slots=True)
class JsonInput:
    value: object


@dataclass(frozen=True, slots=True)
class FormInput:
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class MultipartInputPart:
    name: str
    content: bytes
    filename: str | None = None
    content_type: str | None = None
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class MultipartInput:
    parts: tuple[MultipartInputPart, ...]
    boundary: str | None = None


@dataclass(frozen=True, slots=True)
class ExactBodyInput:
    content: bytes
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class StreamBodyInput:
    factory: Callable[[], BinaryIO]
    known_length: int | None
    media_type: str | None = None


type ZaprosBodyInput = (
    NoBodyInput | JsonInput | FormInput | MultipartInput | ExactBodyInput | StreamBodyInput
)


__all__ = [
    "ExactBodyInput",
    "FormInput",
    "JsonInput",
    "MultipartInput",
    "MultipartInputPart",
    "NoBodyInput",
    "StreamBodyInput",
    "ZaprosBodyInput",
]
