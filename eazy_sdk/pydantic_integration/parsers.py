"""Optional Pydantic type-adapter loading used by extraction and typed headers."""

from __future__ import annotations

from functools import cache
from typing import Any


@cache
def get_type_adapter(annotation: Any) -> Any:
    try:
        from pydantic import TypeAdapter
    except ImportError as exc:
        raise RuntimeError("Pydantic validation requires the 'pydantic' extra") from exc
    return TypeAdapter(annotation)


__all__ = ["get_type_adapter"]
