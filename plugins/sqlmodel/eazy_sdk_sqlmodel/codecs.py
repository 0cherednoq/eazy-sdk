"""Typed JSON codecs for the SQLModel account backend."""

from __future__ import annotations

import base64
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, SecretBytes, SecretStr


class SqlValueCodecError(TypeError):
    """The default codec cannot represent or validate a persisted value."""


class SqlValueCodec[T](Protocol):
    """One typed value encoded as a versioned JSON object."""

    def encode(self, value: T) -> dict[str, object]: ...

    def decode(self, value: Mapping[str, object]) -> T: ...


def _json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SqlValueCodecError("non-finite floats require a custom codec")
        return value
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, SecretBytes):
        return {
            "$eazy_sdk": "secret-bytes",
            "base64": base64.b64encode(value.get_secret_value()).decode("ascii"),
        }
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise SqlValueCodecError("naive datetimes require a custom codec")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, BaseModel):
        return {name: _json_value(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SqlValueCodecError("non-string mapping keys require a custom codec")
            result[key] = _json_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_value(item) for item in value]
    raise SqlValueCodecError(f"unsupported value of type {type(value).__name__}")


def _restore_tags(value: object) -> object:
    if isinstance(value, list):
        return [_restore_tags(item) for item in value]
    if isinstance(value, dict):
        if value.get("$eazy_sdk") == "secret-bytes" and set(value) == {"$eazy_sdk", "base64"}:
            encoded = value.get("base64")
            if not isinstance(encoded, str):
                raise SqlValueCodecError("invalid secret-bytes payload")
            try:
                return base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise SqlValueCodecError("invalid secret-bytes payload") from exc
        return {str(key): _restore_tags(item) for key, item in value.items()}
    return value


class PlainPydanticCodec[T: BaseModel]:
    """Persist one concrete Pydantic model as plaintext versioned JSON.

    Secret values are deliberately persisted in full, but this object and its errors never include
    user values in their representation.
    """

    def __init__(self, model: type[T]) -> None:
        self._model = model

    def __repr__(self) -> str:
        return f"PlainPydanticCodec(model={self._model.__name__})"

    def encode(self, value: T) -> dict[str, object]:
        parsed = self._model.model_validate(value)
        data = _json_value(parsed)
        if not isinstance(data, dict):
            raise SqlValueCodecError("Pydantic model must encode to an object")
        return {"format": "plain-json", "version": 1, "data": data}

    def decode(self, value: Mapping[str, object]) -> T:
        if value.get("format") != "plain-json" or value.get("version") != 1:
            raise SqlValueCodecError("unsupported plaintext payload envelope")
        data = value.get("data")
        if not isinstance(data, Mapping):
            raise SqlValueCodecError("plaintext payload data must be an object")
        try:
            return self._model.model_validate(_restore_tags(dict(data)))
        except Exception:
            raise SqlValueCodecError(
                f"stored payload is invalid for {self._model.__name__}"
            ) from None


__all__ = ["PlainPydanticCodec", "SqlValueCodec", "SqlValueCodecError"]
