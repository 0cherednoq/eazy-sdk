"""Compile and execute framework-neutral HTML extraction schemas."""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Annotated, Any, Union, cast, get_args, get_origin

from eazy_sdk.models import (
    ModelAdapterRegistry,
    ModelField,
    UnsupportedModelTypeError,
    default_model_adapters,
)


@dataclass(frozen=True, slots=True)
class CSS:
    expression: str

    def __post_init__(self) -> None:
        if not self.expression:
            raise ValueError("CSS expression must not be empty")


@dataclass(frozen=True, slots=True)
class XPath:
    expression: str

    def __post_init__(self) -> None:
        if not self.expression:
            raise ValueError("XPath expression must not be empty")


type SelectorMarker = CSS | XPath


@dataclass(frozen=True, slots=True)
class Scope:
    selector: SelectorMarker


class ExtractionCompileError(TypeError):
    pass


class ExtractionError(ValueError):
    def __init__(
        self,
        path: tuple[str, ...],
        message: str,
        *,
        selector: SelectorMarker | None = None,
    ) -> None:
        self.path = path
        self.selector = selector
        super().__init__(f"{'.'.join(path)}: {message}")


@dataclass(frozen=True, slots=True)
class ExtractionField:
    model_field: ModelField
    annotation: object
    selector: SelectorMarker | None
    scope: Scope | None
    nested: ExtractionSchema | None
    many: bool
    optional: bool


@dataclass(frozen=True, slots=True)
class ExtractionSchema:
    model: type[object]
    fields: tuple[ExtractionField, ...]


def compile_extraction_schema(
    model: type[object],
    *,
    models: ModelAdapterRegistry | None = None,
) -> ExtractionSchema:
    registry = models or default_model_adapters()
    return _compile_model(model, registry, stack=())


def parse_html[T](
    html: bytes | str,
    model: type[T],
    *,
    models: ModelAdapterRegistry | None = None,
) -> T:
    registry = models or default_model_adapters()
    document = HtmlDocument(html)
    return document.load(model, models=registry)


class HtmlDocument:
    def __init__(self, html: bytes | str) -> None:
        try:
            from parsel import Selector
        except ImportError as exc:
            raise RuntimeError("HTML extraction requires eazy-sdk[html]") from exc
        text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
        self._root = _ParselNode(Selector(text=text, type="html"))

    def load[T](self, model: type[T], *, models: ModelAdapterRegistry) -> T:
        primitive = self.extract(cast(type[object], model), models=models)
        return models.load(model, primitive)

    def extract(self, model: type[object], *, models: ModelAdapterRegistry) -> dict[str, object]:
        schema = compile_extraction_schema(model, models=models)
        return _extract_model(schema, self._root, path=(model.__name__,))


@dataclass(frozen=True, slots=True)
class _ParselNode:
    selector: Any

    def values(self, marker: SelectorMarker) -> tuple[str, ...]:
        selected = (
            self.selector.css(marker.expression)
            if isinstance(marker, CSS)
            else self.selector.xpath(marker.expression)
        )
        return tuple(str(value) for value in selected.getall())

    def nodes(self, marker: SelectorMarker) -> tuple[_ParselNode, ...]:
        selected = (
            self.selector.css(marker.expression)
            if isinstance(marker, CSS)
            else self.selector.xpath(marker.expression)
        )
        return tuple(_ParselNode(node) for node in selected)


def _compile_model(
    model: type[object],
    models: ModelAdapterRegistry,
    *,
    stack: tuple[type[object], ...],
) -> ExtractionSchema:
    if model in stack:
        raise ExtractionCompileError(f"recursive HTML model is unsupported: {model.__name__}")
    try:
        fields = models.fields(model)
    except UnsupportedModelTypeError as exc:
        raise ExtractionCompileError(f"unsupported HTML model: {model.__name__}") from exc
    output: list[ExtractionField] = []
    for field in fields:
        annotation, annotation_metadata = _unwrap(field.annotation)
        metadata = (*field.metadata, *annotation_metadata)
        selectors = tuple(item for item in metadata if isinstance(item, CSS | XPath))
        scopes = tuple(item for item in metadata if isinstance(item, Scope))
        if len(selectors) > 1 or len(scopes) > 1 or (selectors and scopes):
            raise ExtractionCompileError(
                f"{model.__name__}.{field.name} has conflicting HTML selector metadata"
            )
        item_type, many, optional = _field_shape(annotation)
        nested = _nested_schema(item_type, models, stack=(*stack, model))
        selector = selectors[0] if selectors else None
        scope = scopes[0] if scopes else None
        if nested is None and selector is None:
            raise ExtractionCompileError(
                f"{model.__name__}.{field.name} requires CSS or XPath metadata"
            )
        if nested is None and scope is not None:
            raise ExtractionCompileError(
                f"{model.__name__}.{field.name} cannot apply Scope to a scalar"
            )
        if nested is not None and many and scope is None:
            raise ExtractionCompileError(
                f"{model.__name__}.{field.name} list of nested models requires Scope"
            )
        output.append(ExtractionField(field, annotation, selector, scope, nested, many, optional))
    return ExtractionSchema(model, tuple(output))


def _nested_schema(
    annotation: object,
    models: ModelAdapterRegistry,
    *,
    stack: tuple[type[object], ...],
) -> ExtractionSchema | None:
    if not isinstance(annotation, type):
        return None
    try:
        models.adapter_for_type(annotation)
    except UnsupportedModelTypeError:
        return None
    return _compile_model(annotation, models, stack=stack)


def _extract_model(
    schema: ExtractionSchema,
    node: _ParselNode,
    *,
    path: tuple[str, ...],
) -> dict[str, object]:
    output: dict[str, object] = {}
    for field in schema.fields:
        field_path = (*path, field.model_field.name)
        if field.nested is not None:
            nodes = node.nodes(field.scope.selector) if field.scope is not None else (node,)
            if field.many:
                output[field.model_field.wire_name] = [
                    _extract_model(field.nested, item, path=(*field_path, str(index)))
                    for index, item in enumerate(nodes)
                ]
                continue
            nested_selected = _one_or_missing(field, nodes, field_path)
            if nested_selected is not None:
                output[field.model_field.wire_name] = _extract_model(
                    field.nested, nested_selected, path=field_path
                )
            continue
        assert field.selector is not None
        values = node.values(field.selector)
        if field.many:
            output[field.model_field.wire_name] = list(values)
            continue
        scalar_selected = _one_or_missing(field, values, field_path)
        if scalar_selected is not None:
            output[field.model_field.wire_name] = scalar_selected
    return output


def _one_or_missing[T](
    field: ExtractionField,
    values: tuple[T, ...],
    path: tuple[str, ...],
) -> T | None:
    if len(values) == 1:
        return values[0]
    if not values and (field.optional or not field.model_field.required):
        return None
    expectation = "one value" if not values else "at most one value"
    raise ExtractionError(
        path,
        f"expected {expectation}, got {len(values)}",
        selector=field.selector or (field.scope.selector if field.scope else None),
    )


def _field_shape(annotation: object) -> tuple[object, bool, bool]:
    annotation, _ = _unwrap(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    optional = False
    if origin in {types.UnionType, Union} and type(None) in args:
        optional = True
        remaining = tuple(item for item in args if item is not type(None))
        if len(remaining) != 1:
            raise ExtractionCompileError("HTML fields support only T | None unions")
        annotation, _ = _unwrap(remaining[0])
        origin = get_origin(annotation)
        args = get_args(annotation)
    if origin is list:
        return (args[0] if args else object), True, optional
    return annotation, False, optional


def _unwrap(annotation: object) -> tuple[object, tuple[object, ...]]:
    metadata: list[object] = []
    while get_origin(annotation) is Annotated:
        annotation, *extras = get_args(annotation)
        metadata.extend(extras)
    return annotation, tuple(metadata)


__all__ = [
    "CSS",
    "ExtractionCompileError",
    "ExtractionError",
    "ExtractionField",
    "ExtractionSchema",
    "HtmlDocument",
    "Scope",
    "XPath",
    "compile_extraction_schema",
    "parse_html",
]
