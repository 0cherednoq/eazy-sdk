"""Sentinel for distinguishing an omitted argument from an explicit value."""

from __future__ import annotations

from typing import Any, Final


class Unset:
    """Singleton marker meaning an argument was not provided."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"

    @classmethod
    def __get_pydantic_core_schema__(cls, source: object, handler: object) -> Any:
        # An operation class may be a Pydantic model; ``Omittable[int]`` then has to be a type
        # Pydantic can build a schema for. Only ever called by Pydantic, so imported lazily.
        from pydantic_core import core_schema

        return core_schema.is_instance_schema(cls)


UNSET: Final[Unset] = Unset()

type Omittable[T] = T | Unset
"""A field the caller may leave out: ``page: Query[Omittable[int]] = UNSET`` sends nothing."""

__all__ = ["UNSET", "Omittable", "Unset"]
