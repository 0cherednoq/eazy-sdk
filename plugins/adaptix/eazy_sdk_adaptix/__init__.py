"""An Adaptix retort as an Eazy SDK model adapter.

An SDK that already keeps one central ``Retort`` — with its ``name_mapping``, its coercions
and its converters — should not describe those rules a second time to serialize a request.
This adapter is the whole bridge: the retort dumps and loads, and the operation declares its
fields as ordinary dataclasses.

The retort serves the types it was configured for and nothing else. Adaptix reads every
dataclass, so an adapter that claimed all of them would collide with the built-in dataclass
adapter on models the retort was never told about. Instead this adapter *stands in for* the
built-in one: it answers for every dataclass, routes the named types through the retort, and
hands the rest to the built-in implementation unchanged. :func:`adaptix_models` installs it
that way, so a registry never holds two adapters claiming the same model.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from adaptix import Retort

from eazy_sdk.models import ModelAdapterRegistry, ModelField
from eazy_sdk.models.adapters import DataclassModelAdapter, ModelAdapter

type ModelDumpMode = Literal["json", "python"]


@dataclass(frozen=True, slots=True)
class AdaptixModelAdapter:
    """Dump and load the named types through ``retort``; every other dataclass is untouched.

    ``names`` is the Python-to-wire table for those types. A retort does not hand its
    ``name_mapping`` back, so the table is declared beside it once — the same list the author
    already writes when configuring the retort.
    """

    retort: Retort
    types: tuple[type[object], ...]
    names: Mapping[type[object], Mapping[str, str]] = field(default_factory=dict)
    name: str = "adaptix"
    builtin: ModelAdapter = field(default_factory=DataclassModelAdapter)

    def __post_init__(self) -> None:
        if not self.types:
            raise ValueError(
                "AdaptixModelAdapter requires the types the retort serves; an adapter that "
                "served none of them would only stand in front of the built-in one"
            )
        if any(not isinstance(item, type) for item in self.types):
            raise ValueError("AdaptixModelAdapter.types accepts classes")

    def serves(self, annotation: object) -> bool:
        """Whether the retort, rather than the built-in adapter, reads this model."""

        return isinstance(annotation, type) and annotation in self.types

    def supports_type(self, annotation: object) -> bool:
        return self.builtin.supports_type(annotation)

    def supports_value(self, value: object) -> bool:
        return self.builtin.supports_value(value)

    def fields(self, annotation: object) -> tuple[ModelField, ...]:
        declared = self.builtin.fields(annotation)
        if not self.serves(annotation):
            return declared
        table = self.names.get(cast(type[object], annotation), {})
        return tuple(
            dataclasses.replace(item, wire_name=table.get(item.name, item.wire_name))
            for item in declared
        )

    def dump(
        self,
        value: object,
        *,
        mode: ModelDumpMode,
        registry: ModelAdapterRegistry,
    ) -> object:
        if not self.serves(type(value)):
            return self.builtin.dump(value, mode=mode, registry=registry)
        return self.retort.dump(value, cast(Any, type(value)))

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        registry: ModelAdapterRegistry,
    ) -> T:
        if not self.serves(annotation):
            return self.builtin.load(annotation, value, registry=registry)
        return self.retort.load(value, annotation)

    def frozen(self, annotation: object) -> bool | None:
        return self.builtin.frozen(annotation)

    def evolve(self, value: object, changes: Mapping[str, object]) -> object:
        return self.builtin.evolve(value, changes)


def adaptix_models(
    models: ModelAdapterRegistry,
    *,
    retort: Retort,
    types: tuple[type[object], ...],
    names: Mapping[type[object], Mapping[str, str]] | None = None,
) -> ModelAdapterRegistry:
    """``models`` with the retort standing in for the built-in dataclass adapter."""

    adapter = AdaptixModelAdapter(retort=retort, types=types, names=names or {})
    kept = tuple(item for item in models.adapters if item.name != "dataclass")
    return ModelAdapterRegistry((*kept, adapter))


__all__ = ["AdaptixModelAdapter", "adaptix_models"]
