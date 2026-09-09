"""What a response model states about itself in its own annotations.

``Const`` is a tag: the JSON Schema ``const`` — or a one-value ``enum`` — written in Python next
to the field that carries it.

::

    class PostMessageOk(msgspec.Struct):
        ok: Annotated[bool, Const(True)]
        channel: str

    class SlackFailure(msgspec.Struct):
        ok: Annotated[bool, Const(False)]
        error: str

A tag states a fact about the body — "this field equals this value" — and never a verdict. Which
of the two is a success is said by the declaration that uses the model, by putting it in
``success=`` or in ``errors=``; the same shape is a success for one operation and a failure for
another, and only the operation knows which. That is why a tag is read the same way for both
kinds of case and is never inverted.

The form is ``Annotated`` rather than ``Literal`` because it has to work on every model backend:
``Literal[True]`` is impossible on msgspec, which accepts only ``None``, integers and strings in
a ``Literal``, and the dataclass and TypedDict adapters do not support ``Literal`` at all. A
one-value ``Literal`` is still read here as the same statement, so a model that spells its tag
that way — and a backend that validates it — is ranked as stating a criterion rather than as
stating nothing.
"""

from __future__ import annotations

import typing
import weakref
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from eazy_sdk.core.errors import PlanError
from eazy_sdk.models.adapters import unroll_alias, unwrap_annotated

__all__ = ["Const", "Tag", "tags_of"]

type TagValue = str | int | float | bool | None

_CONSTANT_TYPES = (str, int, float, bool, type(None))


@dataclass(frozen=True, slots=True)
class Const:
    """The constant a field must equal for its case to claim the response."""

    value: TagValue

    def __post_init__(self) -> None:
        if not isinstance(self.value, _CONSTANT_TYPES):
            raise PlanError(
                f"Const({self.value!r}) is not a constant a schema states: a tag holds a string, "
                "a number, a boolean or None"
            )

    def __repr__(self) -> str:
        return f"Const({self.value!r})"


@dataclass(frozen=True, slots=True)
class Tag:
    """One field of a parsed value that must equal a constant."""

    name: str
    value: TagValue

    def holds(self, value: object) -> bool:
        """Whether the parsed value carries this tag.

        A missing field is a no rather than an error: a value that does not have the field the
        tag names is simply another case. The types must match as well as the values, because
        ``1 == True`` in Python and a body carrying ``{"success": 1}`` must not satisfy
        ``Const(True)``.
        """

        try:
            actual = value[self.name] if isinstance(value, Mapping) else getattr(value, self.name)
        except (KeyError, AttributeError, TypeError):
            return False
        return type(actual) is type(self.value) and actual == self.value

    def __repr__(self) -> str:
        return f"<{self.name} == {self.value!r}>"


_TAGS: weakref.WeakKeyDictionary[type, tuple[Tag, ...]] = weakref.WeakKeyDictionary()
"""Tags read once per model class. Resolving annotations is what phase 51 had to stop repeating."""


def tags_of(model: object) -> tuple[Tag, ...]:
    """The tags a model class declares, its base classes included, or ``()`` for none.

    A failure to read is raised, never remembered: an annotation that does not resolve yet must
    keep failing until someone resolves it, and then start working.
    """

    if not isinstance(model, type):
        return ()
    with suppress(TypeError):  # a class that cannot be weakly referenced, as a few C types cannot
        remembered = _TAGS.get(model)
        if remembered is not None:
            return remembered
    read = _read_tags(model)
    with suppress(TypeError):
        _TAGS[model] = read
    return read


def _read_tags(model: type) -> tuple[Tag, ...]:
    hints = typing.get_type_hints(model, include_extras=True)
    tags: list[Tag] = []
    for name, hint in hints.items():
        declared, metadata = unwrap_annotated(unroll_alias(hint))
        constants = [item for item in metadata if isinstance(item, Const)]
        if len(constants) > 1:
            raise PlanError(
                f"{model.__name__}.{name} declares {len(constants)} constants; a field states one"
            )
        if constants:
            value = constants[0].value
            _refuse_impossible_constant(model, name, declared, value)
            tags.append(Tag(name, value))
            continue
        literal = _one_value_literal(declared)
        if literal is not _NOTHING:
            tags.append(Tag(name, typing.cast(TagValue, literal)))
    return tuple(tags)


class _Nothing:
    """A distinct absence, because ``None`` is itself a constant a field can be tagged with."""


_NOTHING = _Nothing()


def _one_value_literal(annotation: object) -> object:
    if get_origin(annotation) is not Literal:
        return _NOTHING
    values = get_args(annotation)
    if len(values) != 1 or not isinstance(values[0], _CONSTANT_TYPES):
        return _NOTHING
    return values[0]


def _refuse_impossible_constant(
    model: type, name: str, annotation: object, value: TagValue
) -> None:
    """A constant the declared field can never hold is a typo, and it is one at import."""

    accepted = _accepted_types(annotation)
    if accepted is None or type(value) in accepted:
        return
    raise PlanError(
        f"Const({value!r}) on {model.__name__}.{name}: "
        f"a {type(value).__name__} is not a {_annotation_text(annotation)}"
    )


def _accepted_types(annotation: object) -> tuple[type, ...] | None:
    """The types the declared field can hold, or ``None`` when the answer is not plain.

    Only a shape the check can be sure about answers here. A generic, a protocol or ``Any`` says
    nothing definite about a constant, and a check that guesses would refuse valid declarations.
    """

    if annotation is Any:
        return None
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        members = get_args(annotation)
        if not all(isinstance(member, type) for member in members):
            return None
        return typing.cast(tuple[type, ...], members)
    if origin is Literal:
        return tuple({type(item) for item in get_args(annotation)})
    if isinstance(annotation, type) and get_origin(annotation) is None:
        return (annotation,)
    return None


def _annotation_text(annotation: object) -> str:
    if isinstance(annotation, type):
        return annotation.__name__
    if get_origin(annotation) is Union or get_origin(annotation) is UnionType:
        return " | ".join(_annotation_text(member) for member in get_args(annotation))
    return str(annotation).replace("typing.", "")
