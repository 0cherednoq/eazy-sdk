"""Normalize ``success=`` / ``errors=`` / ``fallback=`` into the ``Responses`` the runtime reads.

The declaration site says *what* comes back — a model, an exception class, a status — and this
module turns it into the one ``Responses`` value the executor already understands. Nothing
here is a second matcher: every helper builds ordinary ``Success``/``Error`` cases.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast, get_args, get_origin

from eazy_sdk.core.errors import PlanError
from eazy_sdk.models import ModelAdapterRegistry

from .cases import (
    DEFAULT,
    ApiError,
    ApiErrorFactory,
    Bytes,
    DefaultStatus,
    Empty,
    Error,
    Extracted,
    Html,
    Json,
    Parsed,
    ResponseRepresentation,
    Responses,
    StatusRange,
    StatusSelector,
    Success,
    Text,
)

_REPRESENTATIONS = (Json, Html, Extracted, Parsed, Text, Bytes, Empty)

type Selector = int | str | StatusRange | DefaultStatus
type Spec = type[object] | None | ResponseRepresentation[Any]
type ErrorSpec = (
    type[BaseException] | Spec | Error[Any] | tuple[type[object], ApiErrorFactory[Any]]
)
type SuccessSpec = Spec | Mapping[Selector, Spec | list[Spec]] | Sequence[Success[Any]]
type ErrorsSpec = Mapping[Selector, ErrorSpec | list[ErrorSpec]] | Sequence[ErrorSpec]
"""One status documents several shapes as a ``list``; a tuple is one spec, never a list of them.

``errors={404: (Problem, Refused)}`` is the ``(model, factory)`` form, so the list is what tells
several cases apart from one pair.
"""


def selector(value: Selector, *, operation_id: str) -> StatusSelector:
    """``"4xx"``/``"5xx"`` become ranges; ints, ranges and ``DEFAULT`` pass through."""

    if isinstance(value, int | StatusRange | DefaultStatus):
        return value
    if (
        isinstance(value, str)
        and len(value) == 3
        and value[0].isdigit()
        and value[1:].lower() == "xx"
    ):
        start = int(value[0]) * 100
        return StatusRange(start, start + 99)
    raise PlanError(f"unsupported status selector {value!r} in {operation_id!r}")


def representation(
    spec: Spec,
    *,
    models: ModelAdapterRegistry,
    unwrap: str | None = None,
    error: bool = False,
) -> ResponseRepresentation[Any]:
    """The representation a bare value stands for; explicit representations pass through.

    ``None`` is an empty body, ``bytes`` and ``str`` the raw body, a model with selector
    metadata is a document, any other model is JSON. Which of the last two applies is decided
    by the model, never declared at the operation.
    """

    if spec is None:
        return Empty()
    if isinstance(spec, _REPRESENTATIONS):
        return spec
    if spec is bytes:
        return Bytes()
    if spec is str:
        return Text()
    from eazy_sdk.models.documents import is_document_model

    if is_document_model(spec, models):
        return Html(cast(type[Any], spec))
    # Anything else the model registry can load: a model class, ``list[Model]``, a union.
    return Json(cast(type[Any], spec), unwrap=None if error else unwrap)


def problem_model(cls: type[Any]) -> type[Any] | None:
    """Read the error model out of ``ApiError[Model]``."""

    for base in getattr(cls, "__orig_bases__", ()):
        origin = get_origin(base)
        if isinstance(origin, type) and issubclass(origin, ApiError):
            arguments = get_args(base)
            if arguments and isinstance(arguments[0], type):
                return arguments[0]
    for base in cls.__mro__[1:]:
        if base is ApiError or not issubclass(base, ApiError):
            continue
        found = problem_model(base)
        if found is not None:
            return found
    return None


def _sequence[T](spec: T | list[T]) -> tuple[T, ...]:
    """A list declares several shapes for one status; anything else is a single spec."""

    return tuple(spec) if isinstance(spec, list) else (spec,)


def _success_cases(
    key: Selector | None,
    spec: Spec | list[Spec],
    *,
    result_type: object | None = None,
    models: ModelAdapterRegistry,
    unwrap: str | None,
    operation_id: str,
) -> tuple[Success[Any], ...]:
    """One status may document several shapes; a list value declares them, ``when=`` tells apart.

    ``key=None`` is the bare form (``success=Json(status=201)``): the representation's own
    ``status`` applies, and a ``Json``/``Html`` without a model reads the operation's result type.
    """

    cases: list[Success[Any]] = []
    for item in _sequence(spec):
        shape = representation(item, models=models, unwrap=unwrap)
        if isinstance(shape, Json | Html) and shape.model is None and result_type is not None:
            shape = replace(shape, model=cast(type[Any], result_type))
        status = (
            selector(key, operation_id=operation_id)
            if key is not None
            else getattr(shape, "status", 200)
        )
        cases.append(Success(status, shape, getattr(shape, "when", None)))
    return tuple(cases)


def error_case(
    status: StatusSelector,
    spec: ErrorSpec,
    *,
    models: ModelAdapterRegistry,
    operation_id: str,
) -> Error[Any]:
    """One error case out of an ``errors=`` entry (see the table in the phase-50 plan, §4.7)."""

    if isinstance(spec, Error):
        return spec
    if isinstance(spec, tuple):
        if len(spec) != 2 or not isinstance(spec[0], type) or not callable(spec[1]):
            raise PlanError(
                f"error entry for {status!r} in {operation_id!r} must be (Model, factory)"
            )
        model, factory = spec
        shape = representation(model, models=models, error=True)
        return Error(status, shape, exception=factory, condition=getattr(shape, "when", None))
    if isinstance(spec, type) and issubclass(spec, ApiError):
        problem = problem_model(spec)
        if problem is None:
            raise PlanError(
                f"{spec.__name__} does not name a problem model; declare "
                f"class {spec.__name__}(ApiError[Model]) or use (Model, factory)"
            )
        shape = representation(problem, models=models, error=True)
        return Error(status, shape, exception=spec, condition=getattr(shape, "when", None))
    if isinstance(spec, type) and issubclass(spec, BaseException):
        raise PlanError(
            f"{spec.__name__} is not an ApiError; declare class {spec.__name__}(ApiError[Model]) "
            "or use (Model, factory)"
        )
    shape = representation(spec, models=models, error=True)
    return Error(status, shape, condition=getattr(shape, "when", None))


def error_cases(
    errors: ErrorsSpec,
    *,
    models: ModelAdapterRegistry,
    operation_id: str,
) -> tuple[Error[Any], ...]:
    """Every ``errors=`` entry as a case; ``DEFAULT`` is refused, that is what ``fallback=`` is."""

    cases: list[Error[Any]] = []
    if isinstance(errors, Mapping):
        entries = [
            (selector(key, operation_id=operation_id), spec)
            for key, value in errors.items()
            for spec in _sequence(value)
        ]
        for status, spec in entries:
            if isinstance(status, DefaultStatus):
                raise PlanError(
                    f"errors= in {operation_id!r} cannot use DEFAULT; declare fallback= instead"
                )
            cases.append(error_case(status, spec, models=models, operation_id=operation_id))
        return tuple(cases)
    for spec in errors:
        if not isinstance(spec, Error):
            raise PlanError(
                f"errors= in {operation_id!r} given as a sequence must hold Error cases; "
                "use a mapping {status: spec} otherwise"
            )
        if isinstance(spec.status, DefaultStatus):
            raise PlanError(
                f"errors= in {operation_id!r} cannot use DEFAULT; declare fallback= instead"
            )
        cases.append(spec)
    return tuple(cases)


def success_cases(
    success: SuccessSpec | None,
    *,
    result_type: object | None,
    models: ModelAdapterRegistry,
    unwrap: str | None,
    operation_id: str,
) -> tuple[Success[Any], ...]:
    if success is None:
        if result_type is None:
            raise PlanError(
                f"operation {operation_id!r} declares neither HttpOperation[T] nor success="
            )
        return _success_cases(
            200,
            cast(Spec, result_type),
            models=models,
            unwrap=unwrap,
            operation_id=operation_id,
        )
    if isinstance(success, Mapping):
        return tuple(
            case
            for key, spec in success.items()
            for case in _success_cases(
                key, spec, models=models, unwrap=unwrap, operation_id=operation_id
            )
        )
    if isinstance(success, list | tuple):
        if not all(isinstance(item, Success) for item in success):
            raise PlanError(
                f"success= in {operation_id!r} given as a sequence must hold Success cases"
            )
        return tuple(success)
    return _success_cases(
        None,
        cast(Spec, success),
        result_type=result_type,
        models=models,
        unwrap=unwrap,
        operation_id=operation_id,
    )


def normalize_responses(
    *,
    result_type: object | None,
    success: SuccessSpec | None,
    errors: ErrorsSpec,
    fallback: ErrorSpec | None,
    models: ModelAdapterRegistry,
    unwrap: str | None,
    operation_id: str,
) -> Responses[Any]:
    """The one ``Responses`` value behind ``success=``/``errors=``/``fallback=``."""

    successes = success_cases(
        success,
        result_type=result_type,
        models=models,
        unwrap=unwrap,
        operation_id=operation_id,
    )
    failures = error_cases(errors, models=models, operation_id=operation_id)
    default = (
        error_case(DEFAULT, fallback, models=models, operation_id=operation_id)
        if fallback is not None
        else None
    )
    return Responses(success=successes, errors=failures, fallback=default)


def result_type_of(success: SuccessSpec | None, generic: object | None) -> object | None:
    """The Python type an operation returns: the generic argument, else what ``success=`` says."""

    if generic is not None:
        return generic
    if success is None:
        return None
    specs: list[Spec | object]
    if isinstance(success, Mapping):
        specs = [item for value in success.values() for item in _sequence(value)]
    elif isinstance(success, list | tuple):
        specs = [
            _representation_result_type(item.response) if isinstance(item, Success) else item
            for item in success
        ]
    else:
        specs = [success]
    types = tuple(dict.fromkeys(_spec_result_type(spec) for spec in specs))
    if not types:
        return None
    if len(types) == 1:
        return types[0]
    try:
        union: object = types[0]
        for item in types[1:]:
            union = union | item  # type: ignore[operator]
        return union
    except TypeError:
        return None


def _spec_result_type(spec: object) -> object:
    if isinstance(spec, _REPRESENTATIONS):
        return _representation_result_type(spec)
    if spec is None:
        return type(None)
    return spec


def _representation_result_type(representation: ResponseRepresentation[Any]) -> object:
    if isinstance(representation, Text):
        return str
    if isinstance(representation, Bytes):
        return bytes
    if isinstance(representation, Empty):
        return type(None)
    return representation.model


__all__: list[str] = []
