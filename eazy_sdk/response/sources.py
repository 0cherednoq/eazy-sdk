"""Fields a model reads from the response around the body: a header, a cookie, the redirect target.

::

    class SignedIn(BaseModel):
        url: Annotated[str, Location(path="/inbox*")]
        session: Annotated[str, FromCookie("sid")]
        request_id: Annotated[str | None, FromHeader("X-Request-Id")] = None

Each marker names where one field comes from. They are applied in one place, after the body has
been read and before the model is loaded, so a JSON model, a document model and a model with no
body at all are filled the same way.

Two kinds of source differ in what a miss means. ``FromHeader`` and ``FromCookie`` name a value
the response was supposed to carry: a required one that is missing is a malformed response.
``Location`` and ``Location.query`` state which response this is: a target that is not the one
described is simply another case, and arbitration carries on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import NoneType, UnionType
from typing import TYPE_CHECKING, Union, get_args, get_origin

from eazy_sdk.core.kernel import NoMatch
from eazy_sdk.exceptions import HeaderValidationError
from eazy_sdk.models import ModelAdapterRegistry, ModelField, UnsupportedModelTypeError
from eazy_sdk.models.adapters import unroll_alias, unwrap_annotated

from .headers import FromHeader
from .location import Location, LocationQuery, resolved_location

if TYPE_CHECKING:
    from .cases import ResponseContext

__all__ = ["FromCookie", "ResponseSource", "apply_response_sources", "is_header_model"]


@dataclass(frozen=True, slots=True)
class FromCookie:
    """Use the value of one cookie this response sets as a model field's input.

    When the response sets the cookie more than once the last value wins, as it does in a
    browser. Expiry, domain and the other attributes are not read here: a cookie that has to
    travel on is the business of ``Cookies(...)``, and this marker only reads a value the SDK
    needs as data, such as a CSRF token to put into a form.
    """

    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("response cookie name cannot be empty")


type ResponseSource = FromHeader | FromCookie | Location | LocationQuery

_SOURCES = (FromHeader, FromCookie, Location, LocationQuery)


def source_of(field: ModelField) -> ResponseSource | None:
    """The one response source a field declares, or ``None`` when the body fills it."""

    _, annotated = unwrap_annotated(unroll_alias(field.annotation))
    found: list[ResponseSource] = []
    for item in (*field.metadata, *annotated):
        if isinstance(item, _SOURCES) and not any(item is seen for seen in found):
            found.append(item)
    if len(found) > 1:
        kinds = {type(item).__name__ for item in found}
        what = "FromHeader" if kinds == {"FromHeader"} else "response"
        raise HeaderValidationError(
            f"Response field {field.name!r} declares multiple {what} sources"
        )
    return found[0] if found else None


def admits_none(annotation: object) -> bool:
    """Whether a field's type lets it stay empty, which is what makes a source optional."""

    annotation, _ = unwrap_annotated(unroll_alias(annotation))
    if annotation is None or annotation is NoneType:
        return True
    if get_origin(annotation) in {Union, UnionType}:
        return any(admits_none(member) for member in get_args(annotation))
    return False


def takes_every_value(annotation: object) -> bool:
    """Whether a field is a list, and so receives every value of a repeated parameter."""

    annotation, _ = unwrap_annotated(unroll_alias(annotation))
    if get_origin(annotation) in {Union, UnionType}:
        return any(
            takes_every_value(member) for member in get_args(annotation) if member is not NoneType
        )
    return annotation is list or get_origin(annotation) is list


def is_header_model(model: object, registry: ModelAdapterRegistry) -> bool:
    """``True`` when every field of ``model`` is read from around the body, so the body is not."""

    if not isinstance(model, type):
        return False
    try:
        fields = registry.fields(model)
    except UnsupportedModelTypeError:
        return False
    return bool(fields) and all(source_of(field) is not None for field in fields)


def apply_response_sources(
    model: type[object],
    value: object,
    context: ResponseContext[object],
    models: ModelAdapterRegistry,
) -> object | NoMatch:
    """Merge the declared sources into the model's input, or say the case is not this response."""

    try:
        fields = models.fields(model)
    except UnsupportedModelTypeError:
        return value

    declared = [(field, source) for field in fields if (source := source_of(field)) is not None]
    if not declared:
        return value
    if not isinstance(value, Mapping):
        raise HeaderValidationError(
            "A response model with FromHeader fields requires a JSON object body"
        )

    merged = dict(value)
    for field, source in declared:
        input_name = field.validation_name or field.name
        merged.pop(field.name, None)
        if input_name != field.name:
            merged.pop(input_name, None)

        if isinstance(source, FromHeader):
            lines = context.headers.getall(source.name)
            if len(lines) > 1:
                raise HeaderValidationError(
                    f"Response header {source.name!r} occurs more than once"
                )
            if lines:
                merged[input_name] = lines[0]
            elif field.required:
                raise HeaderValidationError(f"Required response header {source.name!r} is missing")
            continue

        if isinstance(source, FromCookie):
            cookies = [found for name, found in context.cookies.values if name == source.name]
            if cookies:
                merged[input_name] = cookies[-1]
            elif field.required:
                raise HeaderValidationError(f"Required response cookie {source.name!r} is missing")
            continue

        located = _located(source, field, context)
        if located is not _MISSING:
            merged[input_name] = located
        elif not admits_none(field.annotation):
            # The field states which response this is, and this is not that response.
            return NoMatch()
        elif field.required:
            merged[input_name] = None
    return merged


class _Missing:
    """A distinct absence, because an empty string is a value a parameter can hold."""


_MISSING = _Missing()


def _located(
    source: Location | LocationQuery, field: ModelField, context: ResponseContext[object]
) -> object:
    target = resolved_location(context)
    if target is None:
        return _MISSING
    if isinstance(source, Location):
        return target.url if source.holds(target) else _MISSING
    values = target.query.get(source.name)
    if values is None:
        return _MISSING
    return list(values) if takes_every_value(field.annotation) else values[0]
