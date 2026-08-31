"""Lossless normalized response headers and typed header descriptors."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from eazy_sdk.exceptions import HeaderValidationError
from eazy_sdk.models import ModelAdapterRegistry, UnsupportedModelTypeError

if TYPE_CHECKING:
    from eazy_sdk.response.normalized import NormalizedResponse


class Headers(Mapping[str, str]):
    """Case-insensitive response headers preserving repeated field lines."""

    def __init__(
        self,
        values: Mapping[Any, Any] | Iterable[tuple[Any, Any]] = (),
    ) -> None:
        items = values.items() if isinstance(values, Mapping) else values
        self._items = tuple((_text(name).lower(), _text(value)) for name, value in items)

    def __getitem__(self, name: str) -> str:
        values = self.getall(name)
        if not values:
            raise KeyError(name)
        return ", ".join(values)

    def __iter__(self) -> Iterator[str]:
        seen: set[str] = set()
        for name, _ in self._items:
            if name not in seen:
                seen.add(name)
                yield name

    def __len__(self) -> int:
        return len(set(self))

    def getall(self, name: str) -> tuple[str, ...]:
        """Return every field line for ``name`` in received order."""
        normalized = name.lower()
        return tuple(value for key, value in self._items if key == normalized)

    def multi_items(self) -> tuple[tuple[str, str], ...]:
        """Return all normalized field lines, including duplicate names."""
        return self._items

    def __repr__(self) -> str:
        return f"Headers({self._items!r})"


def _text(value: object) -> str:
    return value.decode("latin-1") if isinstance(value, bytes) else str(value)


@dataclass(frozen=True, slots=True)
class FromHeader:
    """Use one exact response-header field line as a model field's input."""

    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("response header name cannot be empty")


def _apply_header_sources(
    model: type[object],
    value: object,
    headers: Headers,
    models: ModelAdapterRegistry,
) -> object:
    """Merge declared exact header sources into a Pydantic model input mapping."""

    try:
        fields = models.fields(model)
    except UnsupportedModelTypeError:
        return value

    declared: list[tuple[str, str, bool, FromHeader]] = []
    for field in fields:
        sources = tuple(item for item in field.metadata if isinstance(item, FromHeader))
        if len(sources) > 1:
            raise HeaderValidationError(
                f"Response field {field.name!r} declares multiple FromHeader sources"
            )
        if sources:
            declared.append(
                (
                    field.name,
                    field.validation_name or field.name,
                    field.required,
                    sources[0],
                )
            )
    if not declared:
        return value
    if not isinstance(value, Mapping):
        raise HeaderValidationError(
            "A response model with FromHeader fields requires a JSON object body"
        )

    merged = dict(value)
    for field_name, input_name, required, source in declared:
        merged.pop(field_name, None)
        if input_name != field_name:
            merged.pop(input_name, None)

        values = headers.getall(source.name)
        if len(values) > 1:
            raise HeaderValidationError(f"Response header {source.name!r} occurs more than once")
        if values:
            merged[input_name] = values[0]
            continue

        if required:
            raise HeaderValidationError(f"Required response header {source.name!r} is missing")
    return merged


@dataclass(frozen=True)
class ResponseHeader[T = str]:
    """A typed documented response-header declaration."""

    name: str
    model: Any = str
    required: bool = False

    def parse(self, response: NormalizedResponse[object]) -> T | None:
        """Read and validate this header from a normalized response."""
        from eazy_sdk.response.normalized import cast_headers

        raw = cast_headers(response.headers).get(self.name)
        if raw is None:
            if self.required:
                raise HeaderValidationError(f"Required response header {self.name!r} is missing")
            return None
        if self.model is str:
            return cast(T, raw)
        from eazy_sdk.pydantic_integration.parsers import get_type_adapter

        try:
            return cast(T, get_type_adapter(self.model).validate_python(raw))
        except Exception as exc:
            raise HeaderValidationError(f"Response header {self.name!r} failed validation") from exc
