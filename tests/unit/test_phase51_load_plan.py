"""Phase 51.3: dataclass and TypedDict adapters load through a precomputed plan.

``DataclassModelAdapter.load`` and ``TypedDictModelAdapter.load`` used to call ``self.fields()``
-- the adapter's own, uncached, ``get_type_hints()``-calling method -- once per object loaded, so
a list of 200 objects resolved the same annotations 200 times (plan §1, F2). The fix precomputes a
load plan per class: one loader per field, chosen once from its annotation, stored in the registry
next to the field cache. These tests are about the one thing that fix is not allowed to touch: the
observable result of loading (P3). None of them assert anything about time; the harness does that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, NotRequired, Required, TypedDict, cast

import pytest

from eazy_sdk.models import ModelAdapterError, ModelAdapterRegistry, default_model_adapters
from eazy_sdk.models.adapters import TypedDictModelAdapter


class Color(Enum):
    RED = "red"
    BLUE = "blue"


@dataclass(frozen=True, slots=True)
class Address:
    street: str
    zip_code: int


@dataclass(frozen=True, slots=True)
class Item:
    identifier: int
    name: str
    address: Address
    tags: list[str]
    scores: dict[str, int]
    coords: tuple[float, float]
    variadic: tuple[int, ...]
    quantity: Decimal
    created: datetime
    color: Color
    note: str | None
    label: Annotated[str, "a marker that must not change what is loaded"]


class AddressTD(TypedDict):
    street: str
    zip_code: int


class ItemTD(TypedDict):
    identifier: int
    name: str
    address: AddressTD
    tags: list[str]
    scores: dict[str, int]
    coords: tuple[float, float]
    variadic: tuple[int, ...]
    quantity: Decimal
    created: datetime
    color: Color
    note: NotRequired[str | None]
    label: Required[Annotated[str, "a marker that must not change what is loaded"]]


RAW = {
    "identifier": 7,
    "name": "widget",
    "address": {"street": "Main St", "zip_code": 12345},
    "tags": ["a", "b"],
    "scores": {"a": 1, "b": 2},
    "coords": [1.5, 2.5],
    "variadic": [1, 2, 3],
    "quantity": "9.99",
    "created": "2026-09-08T00:00:00",
    "color": "red",
    "note": None,
    "label": "L",
}


def _assert_loaded_item(loaded: object) -> None:
    assert isinstance(loaded, (Item, dict))
    if isinstance(loaded, Item):
        address, tags, scores, coords, variadic = (
            loaded.address,
            loaded.tags,
            loaded.scores,
            loaded.coords,
            loaded.variadic,
        )
        identifier, name, quantity, created, color, note, label = (
            loaded.identifier,
            loaded.name,
            loaded.quantity,
            loaded.created,
            loaded.color,
            loaded.note,
            loaded.label,
        )
    else:
        address, tags, scores, coords, variadic = (
            loaded["address"],
            loaded["tags"],
            loaded["scores"],
            loaded["coords"],
            loaded["variadic"],
        )
        identifier, name, quantity, created, color, note, label = (
            loaded["identifier"],
            loaded["name"],
            loaded["quantity"],
            loaded["created"],
            loaded["color"],
            loaded["note"],
            loaded["label"],
        )
    assert identifier == 7
    assert name == "widget"
    assert isinstance(address, (Address, dict))
    street = address.street if isinstance(address, Address) else address["street"]
    zip_code = address.zip_code if isinstance(address, Address) else address["zip_code"]
    assert street == "Main St"
    assert zip_code == 12345
    assert tags == ["a", "b"]
    assert scores == {"a": 1, "b": 2}
    assert coords == (1.5, 2.5)
    assert variadic == (1, 2, 3)
    assert quantity == Decimal("9.99")
    assert created == datetime(2026, 9, 8)
    assert color is Color.RED
    assert note is None
    assert label == "L"


@pytest.mark.parametrize("target", [Item, ItemTD], ids=["dataclass", "typed-dict"])
def test_nested_models_lists_dicts_tuples_optional_enum_decimal_datetime_load_correctly(
    target: type[object],
) -> None:
    """Every shape the plan names (§6, 51.3 DoD) still loads to the same result (P3)."""

    registry = default_model_adapters()

    loaded = registry.load(target, RAW)

    _assert_loaded_item(loaded)


@pytest.mark.parametrize("target", [Item, ItemTD], ids=["dataclass", "typed-dict"])
def test_annotated_field_metadata_still_unwraps(target: type[object]) -> None:
    """The ``label`` field is ``Annotated``; the plan must load it as plain ``str`` regardless."""

    registry = default_model_adapters()

    loaded = registry.load(target, RAW)
    label = loaded.label if isinstance(loaded, Item) else cast(Any, loaded)["label"]

    assert label == "L"


def test_dataclass_missing_required_field_raises_the_same_error_text() -> None:
    registry = default_model_adapters()
    incomplete = {key: value for key, value in RAW.items() if key != "name"}

    with pytest.raises(ModelAdapterError, match=r"missing required field Item\.name"):
        registry.load(Item, incomplete)


def test_typed_dict_missing_required_field_raises_the_same_error_text() -> None:
    registry = default_model_adapters()
    incomplete = {key: value for key, value in RAW.items() if key != "name"}

    with pytest.raises(ModelAdapterError, match=r"missing required field ItemTD\.name"):
        registry.load(ItemTD, incomplete)


def test_typed_dict_unknown_field_raises_the_same_error_text() -> None:
    registry = default_model_adapters()
    extra = {**RAW, "surprise": 1}

    with pytest.raises(ModelAdapterError, match=r"unknown fields for ItemTD: \['surprise'\]"):
        registry.load(ItemTD, extra)


def test_typed_dict_optional_field_may_be_omitted() -> None:
    registry = default_model_adapters()
    without_note = {key: value for key, value in RAW.items() if key != "note"}

    loaded = registry.load(ItemTD, without_note)

    assert "note" not in loaded


@pytest.mark.parametrize("target", [Item, ItemTD], ids=["dataclass", "typed-dict"])
def test_the_load_plan_is_built_once_and_remembered(target: type[object]) -> None:
    """The plan is cached beside the field cache, keyed by class, same as plan §6 requires."""

    registry = default_model_adapters()
    if registry._load_plans is None:
        pytest.skip("EAZY_SDK_NO_FIELD_CACHE=1 for this run")
    registry.clear_field_cache()

    registry.load(target, RAW)
    assert target in registry._load_plans

    plan = registry._load_plans[target]
    registry.load(target, RAW)

    assert registry._load_plans[target] is plan


def test_clear_field_cache_also_forgets_load_plans() -> None:
    registry = default_model_adapters()
    registry.load(Item, RAW)
    registry.load(ItemTD, RAW)
    if registry._load_plans is None:
        pytest.skip("EAZY_SDK_NO_FIELD_CACHE=1 for this run")
    assert len(registry._load_plans) >= 2

    registry.clear_field_cache()

    assert len(registry._load_plans) == 0
    # ... and loading still works once the plan has to be rebuilt.
    _assert_loaded_item(registry.load(Item, RAW))


def test_the_cache_can_be_turned_off_for_load_plans_too() -> None:
    """Same switch as the field cache (P4): disabled, a plan is rebuilt every call."""

    disabled = ModelAdapterRegistry(default_model_adapters().adapters, None, None)

    first = disabled.load(Item, RAW)
    second = disabled.load(Item, RAW)

    assert first == second
    assert disabled._load_plans is None


def test_typed_dict_adapters_own_fields_and_the_registrys_plan_agree() -> None:
    """The plan is built from ``registry.fields()``; the adapter's own method must still match."""

    registry = default_model_adapters()
    adapter = next(a for a in registry.adapters if isinstance(a, TypedDictModelAdapter))

    assert [field.name for field in adapter.fields(ItemTD)] == [
        field.name for field in registry.fields(ItemTD)
    ]
