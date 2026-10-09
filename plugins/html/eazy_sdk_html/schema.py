"""Compile and execute framework-neutral HTML extraction schemas."""

from __future__ import annotations

import re
import types
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, ClassVar, Union, cast, get_args, get_origin

from eazy_sdk.models import (
    ModelAdapterRegistry,
    ModelField,
    UnsupportedModelTypeError,
    default_model_adapters,
)
from eazy_sdk.response.sources import source_of
from eazy_sdk.serialization import DocumentBackend, DocumentNode, SelectorMarker


@dataclass(frozen=True, slots=True)
class CSS:
    expression: str

    language: ClassVar[str] = "css"

    def __post_init__(self) -> None:
        if not self.expression:
            raise ValueError("CSS expression must not be empty")


@dataclass(frozen=True, slots=True)
class XPath:
    expression: str

    language: ClassVar[str] = "xpath"

    def __post_init__(self) -> None:
        if not self.expression:
            raise ValueError("XPath expression must not be empty")


@lru_cache(maxsize=512)
def _pattern(expression: str, flags: int) -> re.Pattern[str]:
    return re.compile(expression, flags)


@dataclass(frozen=True, slots=True)
class Regex:
    """Select by a regular expression over the text of the document as it was received.

    For what no markup addresses: a token inside a script, a value in an inline JSON blob. With
    one group the field receives the group, with none the whole match. A single-value field
    takes the first match, a list field every match.

    The text is searched as received: entities are not decoded and scripts are not parsed, so
    the pattern sees an ``&amp;`` or an escaped character exactly as the service wrote it.
    """

    expression: str
    flags: int = 0

    language: ClassVar[str] = "regex"

    def __post_init__(self) -> None:
        if not self.expression:
            raise ValueError("Regex expression must not be empty")
        try:
            compiled = _pattern(self.expression, self.flags)
        except re.error as exc:
            raise ExtractionCompileError(
                f"Regex({self.expression!r}) does not compile: {exc}"
            ) from exc
        if compiled.groups > 1:
            raise ExtractionCompileError(
                f"Regex({self.expression!r}) has {compiled.groups} groups; a field takes one "
                "value, so the pattern keeps one group or none"
            )

    def search(self, text: str) -> tuple[str, ...]:
        """Every match in order: the group when the pattern has one, the whole match otherwise."""

        compiled = _pattern(self.expression, self.flags)
        index = 1 if compiled.groups else 0
        return tuple(match.group(index) or "" for match in compiled.finditer(text))


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

    @property
    def has_required_field(self) -> bool:
        """Whether the model can fail to extract, and so tell one document from another.

        A model whose fields are all optional — or all lists, which may legitimately come
        back empty — extracts successfully from any page at all, including an error page.
        Such a model cannot be a response case on its own.
        """

        return any(
            not field.optional
            and not field.many
            and (field.selector is not None or field.scope is not None)
            for field in self.fields
        )


@dataclass(frozen=True, slots=True)
class ParselBackend:
    """The parser this plugin brings; parsel reads both selector languages."""

    name: str = "parsel"

    @property
    def selector_languages(self) -> frozenset[str]:
        return frozenset({"css", "xpath", "regex"})

    @property
    def media_types(self) -> frozenset[str]:
        return frozenset({"text/html", "application/xhtml+xml"})

    def parse(self, data: bytes | str) -> DocumentNode:
        from parsel import Selector

        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        return ParselNode(Selector(text=text, type="html"), text)


DEFAULT_HTML_BACKEND: DocumentBackend = ParselBackend()


def compile_extraction_schema(
    model: type[object],
    *,
    models: ModelAdapterRegistry | None = None,
    backend: DocumentBackend | None = None,
) -> ExtractionSchema:
    registry = models or default_model_adapters()
    schema = _compile_model(model, registry, stack=())
    _check_selector_languages(schema, backend or DEFAULT_HTML_BACKEND, seen=set())
    return schema


def parse_html[T](
    html: bytes | str,
    model: type[T],
    *,
    models: ModelAdapterRegistry | None = None,
    backend: DocumentBackend | None = None,
) -> T:
    registry = models or default_model_adapters()
    document = HtmlDocument(html, backend=backend)
    return document.load(model, models=registry)


class HtmlDocument:
    def __init__(self, html: bytes | str, *, backend: DocumentBackend | None = None) -> None:
        self._backend = backend or DEFAULT_HTML_BACKEND
        self._root = self._backend.parse(html)

    def load[T](self, model: type[T], *, models: ModelAdapterRegistry) -> T:
        primitive = self.extract(cast(type[object], model), models=models)
        return models.load(model, primitive)

    def extract(self, model: type[object], *, models: ModelAdapterRegistry) -> dict[str, object]:
        schema = compile_extraction_schema(model, models=models, backend=self._backend)
        return _extract_model(schema, self._root, path=(model.__name__,))


def _check_selector_languages(
    schema: ExtractionSchema,
    backend: DocumentBackend,
    *,
    seen: set[type[object]],
) -> None:
    """Reject a selector the chosen parser cannot speak, while it is still a declaration."""

    if schema.model in seen:
        return
    seen.add(schema.model)
    for field in schema.fields:
        for marker in (field.selector, field.scope.selector if field.scope else None):
            if marker is None or marker.language in backend.selector_languages:
                continue
            spoken = ", ".join(sorted(backend.selector_languages)) or "no selector language"
            raise ExtractionCompileError(
                f"{schema.model.__name__}.{field.model_field.name} selects by "
                f"{marker.language!r}, which the {backend.name!r} document backend cannot "
                f"read (it speaks {spoken})"
            )
        if field.nested is not None:
            _check_selector_languages(field.nested, backend, seen=seen)


@dataclass(frozen=True, slots=True)
class ParselNode:
    selector: Any
    source: str | None = None
    """The text the document was parsed from; only the root has it, a nested node is markup."""

    def values(self, marker: SelectorMarker) -> tuple[str, ...]:
        if isinstance(marker, Regex):
            # The root is searched as received; a node under a scope has only its own markup.
            text = self.source if self.source is not None else str(self.selector.get())
            return marker.search(text)
        return tuple(str(value) for value in self._select(marker).getall())

    def nodes(self, marker: SelectorMarker) -> tuple[ParselNode, ...]:
        return tuple(ParselNode(node) for node in self._select(marker))

    def _select(self, marker: SelectorMarker) -> Any:
        if marker.language == "css":
            return self.selector.css(marker.expression)
        return self.selector.xpath(marker.expression)


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
        selectors = tuple(item for item in metadata if isinstance(item, CSS | XPath | Regex))
        scopes = tuple(item for item in metadata if isinstance(item, Scope))
        if len(selectors) > 1 or len(scopes) > 1 or (selectors and scopes):
            raise ExtractionCompileError(
                f"{model.__name__}.{field.name} has conflicting HTML selector metadata"
            )
        if _read_around_the_body(model, field, selectors, scopes):
            continue
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


def _read_around_the_body(
    model: type[object],
    field: ModelField,
    selectors: tuple[object, ...],
    scopes: tuple[Scope, ...],
) -> bool:
    """Whether the document says nothing about this field; refuses the two impossible mixes.

    A field read from a header, a cookie or the redirect target is merged in after extraction,
    so it takes no part in the schema.
    """

    if source_of(field) is not None:
        if selectors or scopes:
            raise ExtractionCompileError(
                f"{model.__name__}.{field.name} is read from the response around the body "
                "and from the document at once; a field has one source"
            )
        return True
    if scopes and isinstance(scopes[0].selector, Regex):
        raise ExtractionCompileError(
            f"{model.__name__}.{field.name}: a Scope selects nodes, and a Regex selects "
            "text; scope by CSS or XPath and read the text inside with Regex"
        )
    return False


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
    node: DocumentNode,
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
        if isinstance(field.selector, Regex):
            # Text repeats where markup does not: a single-value field takes the first match.
            values = values[:1]
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
    "DEFAULT_HTML_BACKEND",
    "ExtractionCompileError",
    "ExtractionError",
    "ExtractionField",
    "ExtractionSchema",
    "HtmlDocument",
    "ParselBackend",
    "ParselNode",
    "Regex",
    "Scope",
    "XPath",
    "compile_extraction_schema",
    "parse_html",
]
