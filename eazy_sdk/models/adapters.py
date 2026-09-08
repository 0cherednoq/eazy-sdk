"""Model-library adapters shared by request, response, and extraction layers."""

from __future__ import annotations

import dataclasses
import os
import types
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import MISSING, dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from typing import (
    Annotated,
    Any,
    Literal,
    NotRequired,
    Protocol,
    ReadOnly,
    Required,
    TypeAliasType,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)
from weakref import WeakKeyDictionary

from eazy_sdk.core.errors import EazySdkError
from eazy_sdk.sentinels import Unset

type ModelDumpMode = Literal["json", "python"]


class ModelAdapterError(EazySdkError, TypeError):
    """Base error for model adapter selection and conversion."""


class UnsupportedModelTypeError(ModelAdapterError):
    """No configured adapter supports a model type or value."""


class AmbiguousModelAdapterError(ModelAdapterError):
    """More than one configured adapter claims the same model."""


@dataclass(frozen=True, slots=True)
class ModelField:
    name: str
    wire_name: str
    annotation: object
    metadata: tuple[object, ...]
    required: bool
    default: object = MISSING
    validation_name: str | None = None


class ModelAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def supports_type(self, annotation: object) -> bool: ...

    def supports_value(self, value: object) -> bool: ...

    def fields(self, annotation: object) -> tuple[ModelField, ...]: ...

    def dump(
        self,
        value: object,
        *,
        mode: ModelDumpMode,
        registry: ModelAdapterRegistry,
    ) -> object: ...

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        registry: ModelAdapterRegistry,
    ) -> T: ...

    def frozen(self, annotation: object) -> bool | None:
        """Whether instances are immutable; ``None`` when the library has no such notion."""
        ...

    def evolve(self, value: object, changes: Mapping[str, object]) -> object:
        """A copy of ``value`` with ``changes`` applied, through the library's own function."""
        ...


type FieldCache = WeakKeyDictionary[type, tuple[ModelField, ...]]


def _new_field_cache() -> FieldCache | None:
    """A per-registry field cache, or ``None`` when the environment asks for none.

    The switch exists so the suite can be run twice, once each way, and prove that the cache
    changes nothing but the time (plan §3, P4). It is deliberately not a parameter, an attribute
    or a documented setting: an author who can turn the cache off in production has been given a
    way to make their SDK slower and nothing else.
    """

    if os.environ.get("EAZY_SDK_NO_FIELD_CACHE") == "1":
        return None
    return WeakKeyDictionary()


type PlannedLoader = Callable[[object, "ModelAdapterRegistry"], object]


@dataclass(frozen=True, slots=True)
class PlannedField:
    """One field of a load plan: a name, its wire name, and a loader chosen once."""

    name: str
    wire_name: str
    load: PlannedLoader
    required: bool
    default: object = MISSING


type LoadPlan = tuple[PlannedField, ...]
type LoadPlanCache = WeakKeyDictionary[type, LoadPlan]


def _new_load_plan_cache() -> LoadPlanCache | None:
    if os.environ.get("EAZY_SDK_NO_FIELD_CACHE") == "1":
        return None
    return WeakKeyDictionary()


type AdapterCache = WeakKeyDictionary[type, "ModelAdapter"]


def _new_adapter_cache() -> AdapterCache | None:
    """A per-registry adapter-selection cache, one for types and a separate one for values.

    Caching a value's adapter by ``type(value)`` rather than the value itself assumes every
    adapter's ``supports_value`` depends only on the value's type -- true of the four adapters
    this module ships (an ``isinstance``/``is_dataclass`` check apiece) and a reasonable contract
    for a third-party one to keep. An adapter that truly needs to look inside the value stays
    correct by being looked up with an explicit name, which bypasses both caches.
    """

    if os.environ.get("EAZY_SDK_NO_FIELD_CACHE") == "1":
        return None
    return WeakKeyDictionary()


def _caches_fields(model: type) -> bool:
    """Whether this model's fields are settled enough to remember.

    A Pydantic model whose forward references have not resolved answers questions about its
    fields provisionally: ``model_rebuild()`` can still change the answer. Everything else has
    been decided by the time the class object exists.
    """

    return getattr(model, "__pydantic_complete__", True) is not False


@dataclass(frozen=True, slots=True)
class ModelAdapterRegistry:
    adapters: tuple[ModelAdapter, ...]
    _fields: FieldCache | None = dataclasses.field(
        default_factory=_new_field_cache, compare=False, repr=False, hash=False
    )
    """Field lists already read, keyed by the model class.

    Excluded from ``compare`` and ``repr`` so that two registries with the same adapters stay
    equal, keep the same hash, and read the same regardless of what either has been asked about.
    Weak keys so a model class built at runtime -- ``pydantic.create_model`` in a test, a factory
    in a plugin -- can still be collected; a registry must not be the reason a class outlives its
    module.
    """
    _load_plans: LoadPlanCache | None = dataclasses.field(
        default_factory=_new_load_plan_cache, compare=False, repr=False, hash=False
    )
    """Load plans already built, keyed by the model class. Same exclusions as ``_fields``."""
    _adapters_by_type: AdapterCache | None = dataclasses.field(
        default_factory=_new_adapter_cache, compare=False, repr=False, hash=False
    )
    """Which adapter answered ``supports_type``, keyed by the annotation. Same exclusions."""
    _adapters_by_value: AdapterCache | None = dataclasses.field(
        default_factory=_new_adapter_cache, compare=False, repr=False, hash=False
    )
    """Which adapter answered ``supports_value``, keyed by ``type(value)``. Same exclusions."""

    def with_adapter(self, adapter: ModelAdapter, *, first: bool = True) -> ModelAdapterRegistry:
        if any(item.name == adapter.name for item in self.adapters):
            raise ValueError(f"duplicate model adapter name: {adapter.name}")
        values = (adapter, *self.adapters) if first else (*self.adapters, adapter)
        return ModelAdapterRegistry(values)

    def replace_adapter(self, name: str, adapter: ModelAdapter) -> ModelAdapterRegistry:
        if adapter.name != name:
            raise ValueError(
                f"replacement model adapter must keep name {name!r}, got {adapter.name!r}"
            )
        if not any(item.name == name for item in self.adapters):
            raise ValueError(f"unknown model adapter: {name!r}")
        return ModelAdapterRegistry(
            tuple(adapter if item.name == name else item for item in self.adapters)
        )

    def fingerprint_components(self) -> tuple[str, ...]:
        return tuple(_adapter_fingerprint(adapter) for adapter in self.adapters)

    def adapter_for_type(self, annotation: object, *, name: str | None = None) -> ModelAdapter:
        return self._select(annotation, by_value=False, name=name)

    def adapter_for_value(self, value: object, *, name: str | None = None) -> ModelAdapter:
        return self._select(value, by_value=True, name=name)

    def fields(self, annotation: object, *, adapter: str | None = None) -> tuple[ModelField, ...]:
        """The model's fields, read once per class and remembered.

        Reading them means resolving the class's annotations, which is the single most expensive
        thing the response path used to do per response -- and the answer depends only on the
        class. Naming an adapter explicitly bypasses the cache: that asks a different question,
        "what would this adapter say", and the answer is not the one worth remembering.
        """

        base, _ = unwrap_annotated(annotation)
        cache = self._fields
        if cache is None or adapter is not None or not isinstance(base, type):
            return self.adapter_for_type(base, name=adapter).fields(base)
        try:
            remembered = cache.get(base)
        except TypeError:
            # A class that cannot be weakly referenced, which a few C types cannot be.
            return self.adapter_for_type(base).fields(base)
        if remembered is not None:
            return remembered
        # Outside the try: a failure to read the fields is raised, never remembered. An
        # unresolved forward reference must keep failing until someone resolves it, and then
        # start working, rather than fail once and forever.
        read = self.adapter_for_type(base).fields(base)
        if _caches_fields(base):
            with suppress(TypeError):
                cache[base] = read
        return read

    def load_plan(self, annotation: type, *, build: Callable[[], LoadPlan]) -> LoadPlan:
        """A model's load plan -- one loader chosen once per field, from its annotation.

        The dataclass and TypedDict adapters call this instead of reading their own fields per
        object: resolving the class's annotations and, for every field, walking the same
        union/list/tuple/dict/scalar dispatch that :meth:`_load` walks, are both facts about the
        class, not about any one object of it. Same rules as :meth:`fields` (plan §3, P4-P7):
        weak keys, no caching while forward references are unresolved, one switch retires both.
        """

        cache = self._load_plans
        if cache is None or not isinstance(annotation, type):
            return build()
        try:
            remembered = cache.get(annotation)
        except TypeError:
            return build()
        if remembered is not None:
            return remembered
        plan = build()
        if _caches_fields(annotation):
            with suppress(TypeError):
                cache[annotation] = plan
        return plan

    def clear_field_cache(self) -> None:
        """Forget every field list, load plan and adapter selection read so far.

        Nothing in the SDK needs this: replacing an adapter builds a new registry, so there is no
        stale entry to invalidate. It exists for the case the cache cannot see -- a class edited
        in place, in a notebook or a test -- and so that "how do I get rid of it" has an answer.
        """

        if self._fields is not None:
            self._fields.clear()
        if self._load_plans is not None:
            self._load_plans.clear()
        if self._adapters_by_type is not None:
            self._adapters_by_type.clear()
        if self._adapters_by_value is not None:
            self._adapters_by_value.clear()

    def dump(
        self,
        value: object,
        *,
        adapter: str | None = None,
        mode: ModelDumpMode = "json",
    ) -> object:
        return self._normalize_dump(value, adapter=adapter, mode=mode)

    def dump_model(
        self,
        value: object,
        *,
        adapter: str | None = None,
        mode: ModelDumpMode = "json",
    ) -> object:
        """Dump one model without recursively converting its field values."""
        selected = self.adapter_for_value(value, name=adapter)
        return selected.dump(value, mode=mode, registry=self)

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        adapter: str | None = None,
    ) -> T:
        return cast(T, self._load(annotation, value, adapter=adapter))

    def evolve[T](self, value: T, /, **changes: object) -> T:
        """A copy of a model value with some fields replaced.

        The field names are checked here, once for every library, so a typo reads the same
        whichever model class the operation is.
        """

        selected = self.adapter_for_value(value)
        names = [field.name for field in selected.fields(type(value))]
        unknown = [name for name in changes if name not in names]
        if unknown:
            raise ModelAdapterError(
                f"{type(value).__name__} has no field {unknown[0]!r}; fields: {', '.join(names)}"
            )
        return cast(T, selected.evolve(value, changes))

    def _select(
        self,
        subject: object,
        *,
        by_value: bool,
        name: str | None,
    ) -> ModelAdapter:
        if name is not None:
            for adapter in self.adapters:
                if adapter.name == name:
                    supported = (
                        adapter.supports_value(subject)
                        if by_value
                        else adapter.supports_type(subject)
                    )
                    if not supported:
                        raise UnsupportedModelTypeError(
                            f"model adapter {name!r} does not support {_type_name(subject)}"
                        )
                    return adapter
            raise UnsupportedModelTypeError(f"unknown model adapter: {name!r}")
        # Naming no adapter is the common case, and the one worth remembering: which adapter
        # answers for a type or a value's type is a fact about that class, resolved by scanning
        # every adapter today so it need not be scanned again tomorrow (plan §1, F6).
        cache = self._adapters_by_value if by_value else self._adapters_by_type
        key = type(subject) if by_value else subject
        if cache is not None and isinstance(key, type):
            try:
                remembered = cache.get(key)
            except TypeError:
                remembered = None
            else:
                if remembered is not None:
                    return remembered
        matches = tuple(
            adapter
            for adapter in self.adapters
            if (adapter.supports_value(subject) if by_value else adapter.supports_type(subject))
        )
        if not matches:
            raise UnsupportedModelTypeError(f"no model adapter supports {_type_name(subject)}")
        if len(matches) > 1:
            names = ", ".join(adapter.name for adapter in matches)
            raise AmbiguousModelAdapterError(
                f"multiple model adapters support {_type_name(subject)}: {names}"
            )
        selected = matches[0]
        if cache is not None and isinstance(key, type):
            with suppress(TypeError):
                cache[key] = selected
        return selected

    def _normalize_dump(
        self,
        value: object,
        *,
        adapter: str | None = None,
        mode: ModelDumpMode,
    ) -> object:
        if value is None or isinstance(value, bool | int | float | str | bytes):
            return value
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, Enum):
            return self._normalize_dump(value.value, mode=mode)
        if isinstance(value, Mapping):
            return {
                str(key): self._normalize_dump(item, mode=mode)
                for key, item in value.items()
                # ``UNSET`` is what "not passed" looks like, so the field is not in the
                # payload at all. An operation rebuilt from its values fills the sentinel
                # back in for whatever the caller left out.
                if not isinstance(item, Unset)
            }
        if _is_sequence(value):
            return [self._normalize_dump(item, mode=mode) for item in cast(Sequence[object], value)]
        selected = self.adapter_for_value(value, name=adapter)
        converted = selected.dump(value, mode=mode, registry=self)
        if converted is value:
            raise ModelAdapterError(f"model adapter {selected.name!r} returned its input unchanged")
        return self._normalize_dump(converted, mode=mode)

    def _load(self, annotation: object, value: object, *, adapter: str | None = None) -> object:
        # ``Omittable[str]`` is an alias for ``str | Unset``: what the field accepts is the
        # value behind the alias, on this side of the boundary as on the HTTP side.
        annotation, _ = unwrap_annotated(unroll_alias(annotation))
        origin = get_origin(annotation)
        args = get_args(annotation)
        if annotation in {Any, object}:
            return value
        if annotation in {dict, list, tuple, set, frozenset}:
            if isinstance(value, annotation):
                return value
            return cast(Callable[[object], object], annotation)(value)
        if origin in {types.UnionType, Union}:
            if value is None and type(None) in args:
                return None
            failures: list[Exception] = []
            for candidate in args:
                if candidate is type(None):
                    continue
                try:
                    return self._load(candidate, value)
                except (TypeError, ValueError) as exc:
                    failures.append(exc)
            raise ModelAdapterError(f"value does not match {_type_name(annotation)}") from (
                failures[-1] if failures else None
            )
        if origin is list:
            if not _is_sequence(value):
                raise ModelAdapterError(f"expected list, got {type(value).__name__}")
            item_type = args[0] if args else object
            return [self._load(item_type, item) for item in cast(Sequence[object], value)]
        if origin is tuple:
            if not _is_sequence(value):
                raise ModelAdapterError(f"expected tuple, got {type(value).__name__}")
            values = cast(Sequence[object], value)
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(self._load(args[0], item) for item in values)
            if args and len(args) != len(values):
                raise ModelAdapterError(f"expected {len(args)} tuple items, got {len(values)}")
            return tuple(
                self._load(item_type, item)
                for item_type, item in zip(args or (object,) * len(values), values, strict=True)
            )
        if origin is dict:
            if not isinstance(value, Mapping):
                raise ModelAdapterError(f"expected mapping, got {type(value).__name__}")
            key_type, item_type = args or (object, object)
            return {
                self._load(key_type, key): self._load(item_type, item)
                for key, item in value.items()
            }
        if annotation is type(None):
            if value is not None:
                raise ModelAdapterError(f"expected None, got {type(value).__name__}")
            return None
        if is_typeddict(annotation):
            selected = self.adapter_for_type(annotation, name=adapter)
            return selected.load(cast(type[Any], annotation), value, registry=self)
        if isinstance(annotation, type) and isinstance(value, annotation):
            return value
        if annotation in {str, int, float, bool, bytes, Decimal, date, datetime}:
            return _load_scalar(cast(type[object], annotation), value)
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return annotation(value)
        if not isinstance(annotation, type):
            raise UnsupportedModelTypeError(f"no model adapter supports {_type_name(annotation)}")
        selected = self.adapter_for_type(annotation, name=adapter)
        return selected.load(annotation, value, registry=self)


def _compile_loader(annotation: object, registry: ModelAdapterRegistry) -> PlannedLoader:
    """Choose, once, the branch of :meth:`ModelAdapterRegistry._load` a field's annotation takes.

    This mirrors ``_load`` branch for branch, using the same precedence, but resolves
    ``unwrap_annotated``/``get_origin``/``get_args`` and the adapter lookup a single time instead
    of on every object a field is read from. Nested annotations (a union candidate, a list's item
    type, a dict's value type) are *not* compiled eagerly -- that would recurse forever on a
    self-referential model -- they go back through ``registry._load``, which is where the next
    optimization (caching adapter selection, plan §6, 51.4) belongs.
    """

    annotation, _ = unwrap_annotated(unroll_alias(annotation))
    if annotation in {Any, object}:
        return lambda value, reg: value
    if annotation in {dict, list, tuple, set, frozenset}:
        ctor = cast(Callable[[object], object], annotation)

        def _bare_container(value: object, reg: ModelAdapterRegistry) -> object:
            return value if isinstance(value, annotation) else ctor(value)

        return _bare_container
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {types.UnionType, Union}:
        candidates = tuple(candidate for candidate in args if candidate is not type(None))
        has_none = type(None) in args

        def _union(value: object, reg: ModelAdapterRegistry) -> object:
            if value is None and has_none:
                return None
            failures: list[Exception] = []
            for candidate in candidates:
                try:
                    return reg._load(candidate, value)
                except (TypeError, ValueError) as exc:
                    failures.append(exc)
            raise ModelAdapterError(f"value does not match {_type_name(annotation)}") from (
                failures[-1] if failures else None
            )

        return _union
    if origin is list:
        item_type = args[0] if args else object

        def _list(value: object, reg: ModelAdapterRegistry) -> object:
            if not _is_sequence(value):
                raise ModelAdapterError(f"expected list, got {type(value).__name__}")
            return [reg._load(item_type, item) for item in cast(Sequence[object], value)]

        return _list
    if origin is tuple:

        def _tuple(value: object, reg: ModelAdapterRegistry) -> object:
            if not _is_sequence(value):
                raise ModelAdapterError(f"expected tuple, got {type(value).__name__}")
            values = cast(Sequence[object], value)
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(reg._load(args[0], item) for item in values)
            if args and len(args) != len(values):
                raise ModelAdapterError(f"expected {len(args)} tuple items, got {len(values)}")
            return tuple(
                reg._load(item_type, item)
                for item_type, item in zip(args or (object,) * len(values), values, strict=True)
            )

        return _tuple
    if origin is dict:
        key_type, item_type = args or (object, object)

        def _dict(value: object, reg: ModelAdapterRegistry) -> object:
            if not isinstance(value, Mapping):
                raise ModelAdapterError(f"expected mapping, got {type(value).__name__}")
            return {
                reg._load(key_type, key): reg._load(item_type, item)
                for key, item in value.items()
            }

        return _dict
    if annotation is type(None):

        def _none(value: object, reg: ModelAdapterRegistry) -> object:
            if value is not None:
                raise ModelAdapterError(f"expected None, got {type(value).__name__}")
            return None

        return _none
    if is_typeddict(annotation):
        selected_typed_dict = registry.adapter_for_type(annotation)

        def _typed_dict(value: object, reg: ModelAdapterRegistry) -> object:
            return selected_typed_dict.load(cast(type[Any], annotation), value, registry=reg)

        return _typed_dict
    if not isinstance(annotation, type):
        raise UnsupportedModelTypeError(f"no model adapter supports {_type_name(annotation)}")
    if annotation in {str, int, float, bool, bytes, Decimal, date, datetime}:

        def _scalar(value: object, reg: ModelAdapterRegistry) -> object:
            if isinstance(value, annotation):
                return value
            return _load_scalar(cast(type[object], annotation), value)

        return _scalar
    if issubclass(annotation, Enum):

        def _enum(value: object, reg: ModelAdapterRegistry) -> object:
            if isinstance(value, annotation):
                return value
            return annotation(value)

        return _enum
    selected_model = registry.adapter_for_type(annotation)

    def _model(value: object, reg: ModelAdapterRegistry) -> object:
        if isinstance(value, annotation):
            return value
        return selected_model.load(annotation, value, registry=reg)

    return _model


@dataclass(frozen=True, slots=True)
class DataclassModelAdapter:
    name: str = "dataclass"

    def supports_type(self, annotation: object) -> bool:
        return isinstance(annotation, type) and dataclasses.is_dataclass(annotation)

    def supports_value(self, value: object) -> bool:
        return not isinstance(value, type) and dataclasses.is_dataclass(value)

    def fields(self, annotation: object) -> tuple[ModelField, ...]:
        if not isinstance(annotation, type) or not dataclasses.is_dataclass(annotation):
            raise UnsupportedModelTypeError("dataclass adapter requires a dataclass type")
        hints = get_type_hints(annotation, include_extras=True)
        output: list[ModelField] = []
        for field in dataclasses.fields(annotation):
            field_type, metadata = unwrap_annotated(hints.get(field.name, field.type))
            required = field.default is MISSING and field.default_factory is MISSING
            default: object = MISSING
            if field.default is not MISSING:
                default = field.default
            output.append(
                ModelField(
                    field.name,
                    field.name,
                    field_type,
                    metadata,
                    required,
                    default,
                    field.name,
                )
            )
        return tuple(output)

    def dump(
        self,
        value: object,
        *,
        mode: ModelDumpMode,
        registry: ModelAdapterRegistry,
    ) -> object:
        return {
            field.name: getattr(value, field.name) for field in dataclasses.fields(cast(Any, value))
        }

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        registry: ModelAdapterRegistry,
    ) -> T:
        if not isinstance(value, Mapping):
            raise ModelAdapterError(
                f"expected mapping for {annotation.__name__}, got {type(value).__name__}"
            )
        plan = registry.load_plan(
            annotation, build=lambda: self._build_load_plan(annotation, registry)
        )
        kwargs: dict[str, object] = {}
        for field in plan:
            if field.wire_name in value:
                kwargs[field.name] = field.load(value[field.wire_name], registry)
            elif field.required:
                raise ModelAdapterError(
                    f"missing required field {annotation.__name__}.{field.name}"
                )
        return annotation(**kwargs)

    def _build_load_plan(self, annotation: type, registry: ModelAdapterRegistry) -> LoadPlan:
        return tuple(
            PlannedField(
                field.name,
                field.wire_name,
                _compile_loader(field.annotation, registry),
                field.required,
                field.default,
            )
            for field in registry.fields(annotation)
        )

    def frozen(self, annotation: object) -> bool | None:
        params = getattr(annotation, "__dataclass_params__", None)
        return bool(params.frozen) if params is not None else None

    def evolve(self, value: object, changes: Mapping[str, object]) -> object:
        return dataclasses.replace(cast(Any, value), **changes)


@dataclass(frozen=True, slots=True)
class PydanticModelAdapter:
    name: str = "pydantic"

    def supports_type(self, annotation: object) -> bool:
        base = _pydantic_base()
        return base is not None and isinstance(annotation, type) and issubclass(annotation, base)

    def supports_value(self, value: object) -> bool:
        base = _pydantic_base()
        return base is not None and isinstance(value, base)

    def fields(self, annotation: object) -> tuple[ModelField, ...]:
        if not self.supports_type(annotation):
            raise UnsupportedModelTypeError("pydantic adapter requires a BaseModel type")
        model = cast(Any, annotation)
        hints = get_type_hints(model, include_extras=True)
        serialize_by_alias = bool(model.model_config.get("serialize_by_alias", False))
        output: list[ModelField] = []
        for name, info in model.model_fields.items():
            field_type, metadata = unwrap_annotated(hints.get(name, info.annotation))
            # Pydantic repeats ``Annotated`` extras in ``FieldInfo.metadata``.
            # The resolved type hint is the authoritative source when it
            # carries metadata, otherwise fall back to FieldInfo (for fields
            # assembled dynamically by Pydantic).
            metadata = metadata or tuple(info.metadata)
            wire_name = (
                info.serialization_alias or info.alias or name if serialize_by_alias else name
            )
            validation_name = (
                info.validation_alias
                if isinstance(info.validation_alias, str)
                else info.alias or name
            )
            output.append(
                ModelField(
                    name,
                    str(wire_name),
                    field_type,
                    metadata,
                    bool(info.is_required()),
                    info.default if not info.is_required() else MISSING,
                    str(validation_name),
                )
            )
        return tuple(output)

    def dump(
        self,
        value: object,
        *,
        mode: ModelDumpMode,
        registry: ModelAdapterRegistry,
    ) -> object:
        return cast(Any, value).model_dump(mode=mode)

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        registry: ModelAdapterRegistry,
    ) -> T:
        return cast(T, cast(Any, annotation).model_validate(value))

    def frozen(self, annotation: object) -> bool | None:
        config = getattr(annotation, "model_config", None)
        return bool(config.get("frozen", False)) if isinstance(config, Mapping) else None

    def evolve(self, value: object, changes: Mapping[str, object]) -> object:
        # ``model_copy`` does not validate, which matches the dataclass and msgspec
        # constructors: an operation value is built, not parsed.
        return cast(Any, value).model_copy(update=dict(changes))


@dataclass(frozen=True, slots=True)
class MsgspecModelAdapter:
    name: str = "msgspec"

    def supports_type(self, annotation: object) -> bool:
        base = _msgspec_struct()
        return base is not None and isinstance(annotation, type) and issubclass(annotation, base)

    def supports_value(self, value: object) -> bool:
        base = _msgspec_struct()
        return base is not None and isinstance(value, base)

    def fields(self, annotation: object) -> tuple[ModelField, ...]:
        if not self.supports_type(annotation):
            raise UnsupportedModelTypeError("msgspec adapter requires a Struct type")
        import msgspec

        output: list[ModelField] = []
        for info in msgspec.structs.fields(cast(Any, annotation)):
            field_type, metadata = unwrap_annotated(info.type)
            output.append(
                ModelField(
                    info.name,
                    info.encode_name,
                    field_type,
                    metadata,
                    bool(info.required),
                    info.default if not info.required else MISSING,
                    info.encode_name,
                )
            )
        return tuple(output)

    def dump(
        self,
        value: object,
        *,
        mode: ModelDumpMode,
        registry: ModelAdapterRegistry,
    ) -> object:
        import msgspec

        return msgspec.to_builtins(value)

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        registry: ModelAdapterRegistry,
    ) -> T:
        import msgspec

        return msgspec.convert(value, type=annotation, strict=False)

    def frozen(self, annotation: object) -> bool | None:
        config = getattr(annotation, "__struct_config__", None)
        return bool(config.frozen) if config is not None else None

    def evolve(self, value: object, changes: Mapping[str, object]) -> object:
        import msgspec

        return msgspec.structs.replace(cast(Any, value), **changes)


@dataclass(frozen=True, slots=True)
class TypedDictModelAdapter:
    """Validate structural mappings without turning request data into model objects."""

    name: str = "typed-dict"

    def supports_type(self, annotation: object) -> bool:
        return is_typeddict(annotation)

    def supports_value(self, value: object) -> bool:
        # A plain dict has no runtime TypedDict identity. Mapping values are
        # handled structurally by the registry before value-based selection.
        return False

    def fields(self, annotation: object) -> tuple[ModelField, ...]:
        if not is_typeddict(annotation):
            raise UnsupportedModelTypeError("TypedDict adapter requires a TypedDict type")
        hints = get_type_hints(annotation, include_extras=True)
        required_keys = cast(frozenset[str], cast(Any, annotation).__required_keys__)
        return tuple(
            ModelField(
                name=name,
                wire_name=name,
                annotation=field_type,
                metadata=metadata,
                required=(name in required_keys if required is None else required),
                default=MISSING,
                validation_name=name,
            )
            for name, declared in hints.items()
            for field_type, metadata, required in (_unwrap_typed_dict_field(declared),)
        )

    def dump(
        self,
        value: object,
        *,
        mode: ModelDumpMode,
        registry: ModelAdapterRegistry,
    ) -> object:
        if not isinstance(value, Mapping):
            raise ModelAdapterError(
                f"expected mapping for TypedDict, got {type(value).__name__}"
            )
        return dict(value)

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        registry: ModelAdapterRegistry,
    ) -> T:
        if not isinstance(value, Mapping):
            raise ModelAdapterError(
                f"expected mapping for {annotation.__name__}, got {type(value).__name__}"
            )
        plan = registry.load_plan(
            annotation, build=lambda: self._build_load_plan(annotation, registry)
        )
        known = {field.name for field in plan}
        unknown = set(value) - known
        if unknown:
            raise ModelAdapterError(
                f"unknown fields for {annotation.__name__}: {sorted(unknown)!r}"
            )
        result: dict[str, object] = {}
        for field in plan:
            if field.name in value:
                result[field.name] = field.load(value[field.name], registry)
            elif field.required:
                raise ModelAdapterError(
                    f"missing required field {annotation.__name__}.{field.name}"
                )
        return cast(T, result)

    def _build_load_plan(self, annotation: type, registry: ModelAdapterRegistry) -> LoadPlan:
        return tuple(
            PlannedField(
                field.name,
                field.wire_name,
                _compile_loader(field.annotation, registry),
                field.required,
                field.default,
            )
            for field in registry.fields(annotation)
        )

    def frozen(self, annotation: object) -> bool | None:
        return None

    def evolve(self, value: object, changes: Mapping[str, object]) -> object:
        if not isinstance(value, Mapping):
            raise ModelAdapterError("TypedDict adapter evolves mappings only")
        return {**value, **changes}


def _adapter_fingerprint(adapter: ModelAdapter) -> str:
    declared_version = getattr(adapter, "version", None)
    if not isinstance(declared_version, str):
        distribution = {"pydantic": "pydantic", "msgspec": "msgspec"}.get(adapter.name)
        if distribution is None:
            declared_version = "builtin-v1"
        else:
            try:
                declared_version = version(distribution)
            except PackageNotFoundError:
                declared_version = "absent"
    implementation = f"{type(adapter).__module__}.{type(adapter).__qualname__}"
    return f"model-adapter:{adapter.name}:{declared_version}:{implementation}"


def unroll_alias(annotation: object) -> object:
    """Substitute a PEP 695 ``type`` alias (``Omittable[int]``) with its value."""

    while True:
        if isinstance(annotation, TypeAliasType):
            annotation = annotation.__value__
            continue
        origin = get_origin(annotation)
        if isinstance(origin, TypeAliasType):
            annotation = origin.__value__[get_args(annotation)]
            continue
        return annotation


def unwrap_annotated(annotation: object) -> tuple[object, tuple[object, ...]]:
    metadata: list[object] = []
    while get_origin(annotation) is Annotated:
        annotation, *extras = get_args(annotation)
        metadata.extend(extras)
    return annotation, tuple(metadata)


@cache
def default_model_adapters() -> ModelAdapterRegistry:
    return ModelAdapterRegistry(
        (
            TypedDictModelAdapter(),
            PydanticModelAdapter(),
            MsgspecModelAdapter(),
            DataclassModelAdapter(),
        )
    )


def _unwrap_typed_dict_field(
    annotation: object,
) -> tuple[object, tuple[object, ...], bool | None]:
    required: bool | None = None
    while get_origin(annotation) in {Required, NotRequired, ReadOnly}:
        if get_origin(annotation) is Required:
            required = True
        elif get_origin(annotation) is NotRequired:
            required = False
        (annotation,) = get_args(annotation)
    field_type, metadata = unwrap_annotated(annotation)
    return field_type, metadata, required


@cache
def _pydantic_base() -> type[object] | None:
    try:
        from pydantic import BaseModel
    except ImportError:
        return None
    return BaseModel


@cache
def _msgspec_struct() -> type[object] | None:
    try:
        import msgspec
    except ImportError:
        return None
    return msgspec.Struct


def _load_scalar(annotation: type[object], value: object) -> object:
    if annotation is str:
        return value if isinstance(value, str) else str(value)
    if annotation is bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
        raise ModelAdapterError(f"cannot convert {type(value).__name__} to bytes")
    if annotation is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        if isinstance(value, int) and value in {0, 1}:
            return bool(value)
        raise ModelAdapterError(f"cannot convert {value!r} to bool")
    if annotation is date and not isinstance(value, datetime):
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if annotation is datetime:
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if isinstance(value, annotation):
        return value
    return cast(Callable[[object], object], annotation)(value)


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _type_name(value: object) -> str:
    if isinstance(value, type):
        return value.__qualname__
    return type(value).__qualname__


__all__ = [
    "AmbiguousModelAdapterError",
    "DataclassModelAdapter",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelAdapterRegistry",
    "ModelDumpMode",
    "ModelField",
    "MsgspecModelAdapter",
    "PydanticModelAdapter",
    "TypedDictModelAdapter",
    "UnsupportedModelTypeError",
    "default_model_adapters",
    "unwrap_annotated",
]
