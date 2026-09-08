"""Phase 51.1: the registry remembers a model's fields, and that changes nothing but the time.

Reading a model's fields resolves its annotations, which the response path was doing once per
response for an answer that depends only on the class. These tests are about what must stay true
once it is remembered: the same fields for every library (P4), no entry outliving the class it
describes (P5), a way to forget (P6), keys that are values rather than addresses (P7), and a
failure that keeps failing until someone fixes it rather than being cached as a verdict.
"""

from __future__ import annotations

import gc
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

import msgspec
import pydantic
import pytest

from eazy_sdk.models import (
    DataclassModelAdapter,
    ModelAdapterRegistry,
    ModelField,
    UnsupportedModelTypeError,
    default_model_adapters,
)


@dataclass(frozen=True, slots=True)
class DataclassItem:
    identifier: int
    name: str


class PydanticItem(pydantic.BaseModel):
    identifier: int
    name: str


class MsgspecItem(msgspec.Struct):
    identifier: int
    name: str


class TypedDictItem(TypedDict):
    identifier: int
    name: str


MODELS = [DataclassItem, PydanticItem, MsgspecItem, TypedDictItem]

CACHING = ModelAdapterRegistry(default_model_adapters().adapters)._fields is not None
"""Whether this run has the cache on. The suite is required to pass either way (P4).

The tests below split in two: what must hold whatever the cache does, and what is about the cache
existing at all. Only the second kind is skipped when the switch is off -- asserting that a second
read returns the very same tuple is a statement about caching, not about fields.
"""

needs_cache = pytest.mark.skipif(not CACHING, reason="EAZY_SDK_NO_FIELD_CACHE=1 for this run")


def _names(fields: tuple[ModelField, ...]) -> list[str]:
    return [field.name for field in fields]


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
@needs_cache
def test_cached_fields_equal_the_uncached_ones(model: type[object]) -> None:
    """P4: the cache is invisible. Reading twice reads the same thing as reading once."""

    cached = default_model_adapters()
    uncached = ModelAdapterRegistry(cached.adapters, None)

    first = cached.fields(model)
    second = cached.fields(model)

    assert first == uncached.fields(model)
    assert second == first
    assert second is first  # the second read is the remembered tuple, not a rebuilt one


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_annotated_models_read_the_same_through_the_cache(model: type[object]) -> None:
    """The cache is keyed on the unwrapped class, so an ``Annotated`` alias resolves to it."""

    registry = default_model_adapters()

    assert registry.fields(Annotated[model, "note"]) == registry.fields(model)


@needs_cache
def test_naming_an_adapter_bypasses_the_cache() -> None:
    """Asking what one adapter says is a different question, and is not remembered as the answer."""

    registry = default_model_adapters()
    registry.fields(DataclassItem)

    explicit = registry.fields(DataclassItem, adapter="dataclass")

    assert _names(explicit) == ["identifier", "name"]


class _RenamingDataclassAdapter(DataclassModelAdapter):
    """A replacement adapter that reports one different field, so a stale entry would show."""

    def fields(self, annotation: object) -> tuple[ModelField, ...]:
        original = super().fields(annotation)
        return tuple(
            ModelField(
                name=field.name,
                wire_name=f"replaced_{field.wire_name}",
                annotation=field.annotation,
                metadata=field.metadata,
                required=field.required,
                default=field.default,
                validation_name=field.validation_name,
            )
            for field in original
        )


def test_replacing_an_adapter_does_not_serve_the_previous_one() -> None:
    """Replacing an adapter builds a new registry, so there is no entry left to go stale."""

    registry = default_model_adapters()
    assert [field.wire_name for field in registry.fields(DataclassItem)] == ["identifier", "name"]

    replaced = registry.replace_adapter("dataclass", _RenamingDataclassAdapter())

    assert [field.wire_name for field in replaced.fields(DataclassItem)] == [
        "replaced_identifier",
        "replaced_name",
    ]
    # ... and the registry that was asked first still answers as it did.
    assert [field.wire_name for field in registry.fields(DataclassItem)] == ["identifier", "name"]


@needs_cache
def test_adding_an_adapter_starts_from_an_empty_cache() -> None:
    """``with_adapter`` builds a new registry too, cache included."""

    registry = default_model_adapters()
    registry.fields(DataclassItem)

    extended = registry.with_adapter(_RenamingDataclassAdapter(name="dataclass-renaming"))

    assert extended._fields is not registry._fields
    assert len(extended._fields or ()) == 0


def test_an_unresolved_forward_reference_keeps_failing_until_it_is_resolved() -> None:
    """A failure is raised, never remembered: the model must start working once it is fixed."""

    registry = default_model_adapters()

    class Deferred(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(defer_build=True)

        later: "LaterDefined"  # noqa: UP037

    with pytest.raises(NameError):
        registry.fields(Deferred)

    class LaterDefined(pydantic.BaseModel):
        value: int

    globals()["LaterDefined"] = LaterDefined  # what resolving a forward reference amounts to
    try:
        Deferred.model_rebuild()

        assert _names(registry.fields(Deferred)) == ["later"]
    finally:
        del globals()["LaterDefined"]


@needs_cache
def test_an_incomplete_model_is_not_remembered() -> None:
    """P4 again, from the other side: what is provisional today must be re-read tomorrow."""

    registry = default_model_adapters()

    class Incomplete(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(defer_build=True)

        later: "NeverDefined"  # type: ignore[name-defined]  # noqa: UP037, F821

    with pytest.raises(NameError):
        registry.fields(Incomplete)

    assert Incomplete not in (registry._fields or {})


@needs_cache
def test_a_model_built_at_runtime_is_collected() -> None:
    """P5: the cache holds classes weakly, so it is never why a class stays alive."""

    registry = default_model_adapters()
    registry.clear_field_cache()

    models = [
        pydantic.create_model(f"Generated{index}", value=(int, ...))
        for index in range(200)
    ]
    for generated in models:
        registry.fields(generated)
    assert len(registry._fields or ()) == 200

    del generated  # the loop variable is a reference too, and it would keep one class alive
    models.clear()
    gc.collect()

    assert len(registry._fields or ()) == 0


@needs_cache
def test_clear_field_cache_forgets_everything() -> None:
    """P6: every cache introduced has a way to be emptied, and it is tested."""

    registry = default_model_adapters()
    for model in MODELS:
        registry.fields(model)
    assert len(registry._fields or ()) >= len(MODELS)

    registry.clear_field_cache()

    assert len(registry._fields or ()) == 0
    assert _names(registry.fields(DataclassItem)) == ["identifier", "name"]


def test_registries_compare_and_hash_on_their_adapters_alone() -> None:
    """P7 and the frozen contract: what a registry has been asked is not part of what it is."""

    first = ModelAdapterRegistry(default_model_adapters().adapters)
    second = ModelAdapterRegistry(default_model_adapters().adapters)
    assert first == second
    assert hash(first) == hash(second)

    first.fields(DataclassItem)

    assert first == second
    assert hash(first) == hash(second)


def test_the_cache_can_be_turned_off_for_the_suite() -> None:
    """The switch the plan requires for running everything twice, both ways (P4)."""

    disabled = ModelAdapterRegistry(default_model_adapters().adapters, None)

    first = disabled.fields(DataclassItem)
    second = disabled.fields(DataclassItem)

    assert first == second
    assert first is not second
    disabled.clear_field_cache()  # a no-op, but it must not raise


@needs_cache
def test_non_class_annotations_are_read_without_being_cached() -> None:
    """``list[T]`` and friends are not classes; they go straight through."""

    registry = default_model_adapters()
    registry.clear_field_cache()

    with pytest.raises(UnsupportedModelTypeError):
        registry.fields(list[DataclassItem])

    assert len(registry._fields or ()) == 0


@needs_cache
def test_msgspec_and_typeddict_fields_survive_a_second_read() -> None:
    """The two adapters that re-read fields per object must keep reading the same ones."""

    registry = default_model_adapters()

    msgspec_fields = registry.fields(MsgspecItem)
    typeddict_fields = registry.fields(TypedDictItem)

    assert _names(msgspec_fields) == ["identifier", "name"]
    assert _names(typeddict_fields) == ["identifier", "name"]
    assert registry.fields(MsgspecItem) is msgspec_fields
    assert registry.fields(TypedDictItem) is typeddict_fields


@needs_cache
def test_a_shared_default_registry_serves_every_caller_the_same_fields() -> None:
    """``default_model_adapters`` is cached, so the cache is shared; that must stay harmless."""

    one: Any = default_model_adapters()
    two: Any = default_model_adapters()

    assert one is two
    assert one.fields(PydanticItem) is two.fields(PydanticItem)
