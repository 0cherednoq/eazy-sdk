"""Model-library adapters shared by request, response, and extraction layers."""

from __future__ import annotations

import dataclasses
import types
from collections.abc import Callable, Mapping, Sequence
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
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

type ModelDumpMode = Literal["json", "python"]


class ModelAdapterError(TypeError):
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


@dataclass(frozen=True, slots=True)
class ModelAdapterRegistry:
    adapters: tuple[ModelAdapter, ...]

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
        base, _ = unwrap_annotated(annotation)
        return self.adapter_for_type(base, name=adapter).fields(base)

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
        return matches[0]

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
            return {str(key): self._normalize_dump(item, mode=mode) for key, item in value.items()}
        if _is_sequence(value):
            return [self._normalize_dump(item, mode=mode) for item in cast(Sequence[object], value)]
        selected = self.adapter_for_value(value, name=adapter)
        converted = selected.dump(value, mode=mode, registry=self)
        if converted is value:
            raise ModelAdapterError(f"model adapter {selected.name!r} returned its input unchanged")
        return self._normalize_dump(converted, mode=mode)

    def _load(self, annotation: object, value: object, *, adapter: str | None = None) -> object:
        annotation, _ = unwrap_annotated(annotation)
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
        kwargs: dict[str, object] = {}
        for field in self.fields(annotation):
            if field.wire_name in value:
                kwargs[field.name] = registry._load(field.annotation, value[field.wire_name])
            elif field.required:
                raise ModelAdapterError(
                    f"missing required field {annotation.__name__}.{field.name}"
                )
        return annotation(**kwargs)


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
        fields = self.fields(annotation)
        known = {field.name for field in fields}
        unknown = set(value) - known
        if unknown:
            raise ModelAdapterError(
                f"unknown fields for {annotation.__name__}: {sorted(unknown)!r}"
            )
        result: dict[str, object] = {}
        for field in fields:
            if field.name in value:
                result[field.name] = registry._load(field.annotation, value[field.name])
            elif field.required:
                raise ModelAdapterError(
                    f"missing required field {annotation.__name__}.{field.name}"
                )
        return cast(T, result)


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
