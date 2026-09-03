"""Declarative HTML extraction plans."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any, Literal, Self, cast

from eazy_sdk.exceptions import ErrorContext

from .exceptions import ExtractionValidationError, MissingExtractedValueError
from .html_inspector import HtmlInspector


@dataclass(frozen=True)
class _Field:
    field: str
    required: bool
    getter: Callable[[HtmlInspector], Any]


class HtmlExtractPlan[T]:
    """Runs a set of extraction steps and optionally validates into a model."""

    def __init__(self, fields: list[_Field], model: type[T] | None) -> None:
        self._fields = fields
        self._model = model

    @classmethod
    def builder[U](cls, model: type[U] | None = None) -> HtmlExtractPlanBuilder[U]:
        return HtmlExtractPlanBuilder(model)

    def run(self, html: HtmlInspector) -> T | dict[str, Any]:
        values: dict[str, Any] = {}
        for field in self._fields:
            value = field.getter(html)
            if value is None and field.required:
                raise MissingExtractedValueError(
                    f"Required extracted field {field.field!r} was not found"
                )
            values[field.field] = value
        if self._model is None:
            return values
        return self._validate(values)

    def _validate(self, values: dict[str, Any]) -> T:
        from eazy_sdk.pydantic_integration.parsers import get_type_adapter

        model = self._model
        if model is None:  # Defensive: callers enter only after ``run`` checked this.
            raise RuntimeError("extract plan has no validation model")
        adapter = get_type_adapter(cast(Hashable, model))
        from pydantic import ValidationError

        try:
            return cast(T, adapter.validate_python(values))
        except ValidationError as exc:
            raise ExtractionValidationError(
                "Extracted values failed model validation",
                context=ErrorContext(
                    rule_name="extract_plan",
                    pydantic_errors=_format_validation_error(exc),
                ),
            ) from exc


class HtmlExtractPlanBuilder[T]:
    """Fluent builder for :class:`HtmlExtractPlan`."""

    def __init__(self, model: type[T] | None = None) -> None:
        self._model = model
        self._fields: list[_Field] = []

    def hidden_input(self, field: str, *, name: str, required: bool = False) -> Self:
        self._fields.append(_Field(field, required, lambda h: h.hidden_input(name)))
        return self

    def meta_content(
        self,
        field: str,
        *,
        name: str | None = None,
        property: str | None = None,
        required: bool = False,
    ) -> Self:
        if name is None and property is None:
            raise ValueError("meta_content requires either name= or property=")
        self._fields.append(
            _Field(field, required, lambda h: h.meta_content(name=name, property=property))
        )
        return self

    def all_urls(self, field: str) -> Self:
        self._fields.append(_Field(field, False, lambda h: h.urls()))
        return self

    def meta_refresh_url(self, field: str, *, required: bool = False) -> Self:
        def getter(h: HtmlInspector) -> Any:
            redirect = h.meta_refresh()
            return redirect.url if redirect is not None else None

        self._fields.append(_Field(field, required, getter))
        return self

    def form_action(self, field: str, *, form_index: int = 0, required: bool = False) -> Self:
        def getter(h: HtmlInspector) -> Any:
            forms = h.forms()
            if form_index < len(forms):
                return forms[form_index].action
            return None

        self._fields.append(_Field(field, required, getter))
        return self

    def regex(
        self,
        field: str,
        pattern: str,
        *,
        group: int | str = 1,
        source: Literal["html", "visible_text"] = "html",
        required: bool = False,
    ) -> Self:
        self._fields.append(
            _Field(field, required, lambda h: h.regex(pattern, group=group, source=source))
        )
        return self

    def build(self) -> HtmlExtractPlan[T]:
        return HtmlExtractPlan(self._fields, self._model)


def _format_validation_error(error: Exception) -> object:
    from eazy_sdk.pydantic_integration.errors import format_validation_error

    return format_validation_error(error)
