"""Which family reads a response model: a document, when its fields carry selectors.

The one implementation of that question. A model whose fields carry ``CSS``/``XPath``
metadata (any object satisfying :class:`~eazy_sdk.serialization.SelectorMarker`, or a
``Scope``-like object holding one) is read by a document backend; every other model is a
structure and is read as JSON. Nothing is declared at the operation.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

from eazy_sdk.models.adapters import ModelAdapterRegistry, UnsupportedModelTypeError
from eazy_sdk.serialization import SelectorMarker


def is_document_model(model: object, registry: ModelAdapterRegistry) -> bool:
    """``True`` when ``model`` or a nested model carries selector metadata on any field."""

    return _walk(model, registry, seen=set())


def _walk(annotation: object, registry: ModelAdapterRegistry, *, seen: set[int]) -> bool:
    if get_origin(annotation) is not None:
        # A generic alias, a union or ``Annotated``: look at what it is made of.
        return any(_walk(item, registry, seen=seen) for item in get_args(annotation))
    if not isinstance(annotation, type) or id(annotation) in seen:
        return False
    seen.add(id(annotation))
    try:
        fields = registry.fields(annotation)
    except UnsupportedModelTypeError:
        return False
    for field in fields:
        if any(_is_selector(item) for item in field.metadata):
            return True
        if _walk(field.annotation, registry, seen=seen):
            return True
    return False


def _is_selector(item: Any) -> bool:
    if isinstance(item, SelectorMarker):
        return True
    selector = getattr(item, "selector", None)
    return selector is not None and isinstance(selector, SelectorMarker)


__all__ = ["is_document_model"]
