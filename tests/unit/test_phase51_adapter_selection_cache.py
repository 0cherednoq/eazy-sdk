"""Phase 51.4 (part 1): the registry remembers which adapter answers for a type or a value.

``_select`` scanned every adapter's ``supports_type``/``supports_value`` on every call -- once per
object loaded or dumped, and again for every nested value inside it (plan §1, F6). These tests are
about what must stay true once the answer is remembered: the same adapter as an uncached lookup
(P4), no entry outliving the class it describes (P5), a way to forget (P6), keys that are values
rather than addresses (P7), and an ambiguous or unsupported subject that keeps failing rather than
being cached as a verdict.
"""

from __future__ import annotations

import gc
from dataclasses import dataclass
from typing import Any, cast

import msgspec
import pydantic
import pytest

from eazy_sdk.models import (
    AmbiguousModelAdapterError,
    DataclassModelAdapter,
    ModelAdapterRegistry,
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


MODELS = [DataclassItem, PydanticItem, MsgspecItem]


def _instance(model: type[object]) -> object:
    return model(identifier=1, name="x")  # type: ignore[call-arg]

CACHING = ModelAdapterRegistry(default_model_adapters().adapters)._adapters_by_type is not None
"""Whether this run has the cache on (P4): the suite must pass either way."""

needs_cache = pytest.mark.skipif(not CACHING, reason="EAZY_SDK_NO_FIELD_CACHE=1 for this run")


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
@needs_cache
def test_cached_selection_agrees_with_the_uncached_one(model: type[object]) -> None:
    """P4: reading twice selects the same adapter an uncached registry would."""

    cached = default_model_adapters()
    uncached = ModelAdapterRegistry(cached.adapters, None, None, None, None)

    by_type = cached.adapter_for_type(model)
    by_value = cached.adapter_for_value(_instance(model))

    assert by_type.name == uncached.adapter_for_type(model).name
    assert by_value.name == uncached.adapter_for_value(_instance(model)).name
    assert cached.adapter_for_type(model) is by_type  # remembered, not re-selected
    assert cached.adapter_for_value(_instance(model)) is by_value


@needs_cache
def test_naming_an_adapter_bypasses_both_caches() -> None:
    """Asking one adapter by name is a different question and is not remembered as the answer."""

    registry = ModelAdapterRegistry(default_model_adapters().adapters)  # a fresh, empty cache

    assert registry.adapter_for_type(DataclassItem, name="dataclass").name == "dataclass"
    assert (
        registry.adapter_for_value(_instance(DataclassItem), name="dataclass").name == "dataclass"
    )
    assert DataclassItem not in (registry._adapters_by_type or {})
    assert DataclassItem not in (registry._adapters_by_value or {})


@needs_cache
def test_replacing_an_adapter_does_not_serve_the_previous_selection() -> None:
    """Replacing an adapter builds a new registry, so there is no stale selection to go wrong."""

    registry = default_model_adapters()
    assert registry.adapter_for_type(DataclassItem).name == "dataclass"

    replaced = registry.replace_adapter("dataclass", DataclassModelAdapter())

    assert replaced._adapters_by_type is not registry._adapters_by_type
    assert len(replaced._adapters_by_type or ()) == 0
    # ... and the registry that was asked first still answers as it did.
    assert registry.adapter_for_type(DataclassItem).name == "dataclass"


@needs_cache
def test_adding_an_adapter_starts_from_an_empty_cache() -> None:
    registry = default_model_adapters()
    registry.adapter_for_type(DataclassItem)

    extended = registry.with_adapter(DataclassModelAdapter(name="dataclass-extra"))

    assert extended._adapters_by_type is not registry._adapters_by_type
    assert len(extended._adapters_by_type or ()) == 0


@needs_cache
def test_a_model_built_at_runtime_is_collected() -> None:
    """P5: the cache holds classes weakly, so it is never why a class stays alive."""

    registry = default_model_adapters()
    registry.clear_field_cache()

    models = [pydantic.create_model(f"Generated{index}", value=(int, ...)) for index in range(200)]
    for generated in models:
        registry.adapter_for_type(generated)
        registry.adapter_for_value(cast(Any, generated)(value=1))
    assert len(registry._adapters_by_type or ()) == 200
    assert len(registry._adapters_by_value or ()) == 200

    del generated
    models.clear()
    gc.collect()

    assert len(registry._adapters_by_type or ()) == 0
    assert len(registry._adapters_by_value or ()) == 0


@needs_cache
def test_clear_field_cache_forgets_adapter_selection_too() -> None:
    """P6: every cache introduced has a way to be emptied, and it is tested."""

    registry = default_model_adapters()
    for model in MODELS:
        registry.adapter_for_type(model)
        registry.adapter_for_value(_instance(model))
    assert len(registry._adapters_by_type or ()) >= len(MODELS)
    assert len(registry._adapters_by_value or ()) >= len(MODELS)

    registry.clear_field_cache()

    assert len(registry._adapters_by_type or ()) == 0
    assert len(registry._adapters_by_value or ()) == 0
    assert registry.adapter_for_type(DataclassItem).name == "dataclass"


def test_registries_compare_and_hash_on_their_adapters_alone() -> None:
    """P7 and the frozen contract: what a registry has been asked is not part of what it is."""

    first = ModelAdapterRegistry(default_model_adapters().adapters)
    second = ModelAdapterRegistry(default_model_adapters().adapters)
    assert first == second
    assert hash(first) == hash(second)

    first.adapter_for_type(DataclassItem)
    first.adapter_for_value(_instance(DataclassItem))

    assert first == second
    assert hash(first) == hash(second)


def test_the_cache_can_be_turned_off_for_adapter_selection_too() -> None:
    """The same switch as the field cache (P4): disabled, selection is re-scanned every call."""

    disabled = ModelAdapterRegistry(default_model_adapters().adapters, None, None, None, None)

    first = disabled.adapter_for_type(DataclassItem)
    second = disabled.adapter_for_type(DataclassItem)

    assert first.name == second.name
    disabled.clear_field_cache()  # a no-op, but it must not raise


@needs_cache
def test_an_unsupported_type_is_not_cached() -> None:
    """A failure is never remembered as an answer -- there is nothing to invalidate later."""

    registry = default_model_adapters()

    with pytest.raises(UnsupportedModelTypeError):
        registry.adapter_for_type(int)

    assert int not in (registry._adapters_by_type or {})


@needs_cache
def test_an_ambiguous_type_is_not_cached() -> None:
    """Two adapters claiming the same class is never memoized as a single verdict."""

    @dataclass(frozen=True, slots=True)
    class Ambiguous:
        value: int

    class _AlsoClaimsDataclasses(DataclassModelAdapter):
        def supports_type(self, annotation: object) -> bool:
            return annotation is Ambiguous

    registry = default_model_adapters().with_adapter(
        _AlsoClaimsDataclasses(name="dataclass-also")
    )

    with pytest.raises(AmbiguousModelAdapterError):
        registry.adapter_for_type(Ambiguous)

    assert Ambiguous not in (registry._adapters_by_type or {})


@needs_cache
def test_a_shared_default_registry_serves_every_caller_the_same_selection() -> None:
    one: Any = default_model_adapters()
    two: Any = default_model_adapters()

    assert one is two
    assert one.adapter_for_type(PydanticItem) is two.adapter_for_type(PydanticItem)
