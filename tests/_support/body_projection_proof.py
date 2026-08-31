"""Isolated BP-00 candidate API; production runtime must not import this module."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class JsonBody:
    """Small stand-in for the production encoding declaration."""

    content_type: str = "application/json"


@dataclass(frozen=True, slots=True)
class BodyProjection[TSource, TWire]:
    """Candidate constructor binding one public source to one private wire target."""

    source: type[TSource]
    target: type[TWire]
    using: Callable[[TSource], TWire]
    encoding: JsonBody
    name: str | None = None

    @property
    def fingerprint_name(self) -> str:
        """Return a stable diagnostic identity without relying on callable module metadata."""

        if self.name is not None:
            return self.name
        callable_name = getattr(self.using, "__name__", type(self.using).__qualname__)
        return (
            f"{self.source.__module__}.{self.source.__qualname__}"
            f"->{self.target.__module__}.{self.target.__qualname__}"
            f":{callable_name}"
        )


__all__ = ["BodyProjection", "JsonBody"]
