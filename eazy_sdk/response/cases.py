"""Unified response cases, parser protocol and typed non-throwing outcomes."""

from __future__ import annotations

import json as json_module
import operator
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property, reduce
from http.cookies import SimpleCookie
from typing import Protocol, cast

from eazy_sdk._internal.kernel import (
    AmbiguousCases,
    MalformedCase,
    NoCaseMatch,
    SelectedCase,
    arbitrate_cases,
)
from eazy_sdk._internal.kernel import (
    Malformed as Malformed,
)
from eazy_sdk._internal.kernel import (
    NoMatch as NoMatch,
)
from eazy_sdk._internal.kernel import (
    ParseAttempt as ParseAttempt,
)
from eazy_sdk._internal.kernel import (
    ParsedValue as ParsedValue,
)
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters

from .headers import Headers, _apply_header_sources
from .normalized import NormalizedResponse, cast_headers


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    number: int


@dataclass(frozen=True, slots=True)
class PreparedRequestSummary:
    method: str
    target: str
    body_length: int


@dataclass(frozen=True, slots=True)
class OperationInfo:
    operation_id: str


@dataclass(frozen=True, slots=True)
class ParsedView[T]:
    value: T | None = None
    error: Exception | None = None


@dataclass(frozen=True)
class ResponseContext[TRaw = object]:
    response: NormalizedResponse[TRaw]
    attempt: AttemptIdentity = AttemptIdentity(1)
    request: PreparedRequestSummary = PreparedRequestSummary("", "", 0)
    operation: OperationInfo = OperationInfo("generic")
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters)
    _artifacts: dict[int, object] = field(default_factory=dict, compare=False, repr=False)

    @cached_property
    def bytes(self) -> bytes:
        return self.response.body

    @cached_property
    def text(self) -> ParsedView[str]:
        try:
            return ParsedView(self.response.text())
        except Exception as exc:
            return ParsedView(error=exc)

    @cached_property
    def json(self) -> ParsedView[object]:
        try:
            return ParsedView(json_module.loads(self.bytes))
        except Exception as exc:
            return ParsedView(error=exc)

    @cached_property
    def headers(self) -> Headers:
        return cast_headers(self.response.headers)

    @cached_property
    def cookies(self) -> ResponseCookies:
        return ResponseCookies.from_headers(self.headers)

    def cached[T](self, identity: object, factory: Callable[[], T]) -> T:
        key = id(identity)
        if key not in self._artifacts:
            self._artifacts[key] = factory()
        return cast(T, self._artifacts[key])


@dataclass(frozen=True, slots=True)
class ResponseCookies:
    values: tuple[tuple[str, str], ...]

    @classmethod
    def from_headers(cls, headers: Headers) -> ResponseCookies:
        output: list[tuple[str, str]] = []
        for line in headers.getall("set-cookie"):
            cookie = SimpleCookie()
            cookie.load(line)
            output.extend((name, morsel.value) for name, morsel in cookie.items())
        return cls(tuple(output))


class BoundResponseParser(Protocol):
    def try_parse[T](self, model: type[T]) -> ParseAttempt[T]: ...


class ResponseParser(Protocol):
    def bind(self, response: ResponseContext[object]) -> BoundResponseParser: ...


class BoundResponseExtractor(Protocol):
    def extract(self, model: type[object]) -> ParseAttempt[object]: ...


class ResponseExtractor(Protocol):
    @property
    def name(self) -> str: ...

    def bind(self, response: ResponseContext[object]) -> BoundResponseExtractor: ...


@dataclass(frozen=True, slots=True)
class CallableParser:
    callback: Callable[[ResponseContext[object], type[object]], ParseAttempt[object]]

    def bind(self, response: ResponseContext[object]) -> BoundResponseParser:
        return _BoundCallableParser(response, self.callback)


@dataclass(frozen=True, slots=True)
class _BoundCallableParser:
    response: ResponseContext[object]
    callback: Callable[[ResponseContext[object], type[object]], ParseAttempt[object]]

    def try_parse[T](self, model: type[T]) -> ParseAttempt[T]:
        return cast(ParseAttempt[T], self.callback(self.response, cast(type[object], model)))


@dataclass(frozen=True, slots=True)
class JsonExtractor:
    name: str = "json"

    def bind(self, response: ResponseContext[object]) -> BoundResponseExtractor:
        return _BoundJsonExtractor(response)


@dataclass(frozen=True, slots=True)
class _BoundJsonExtractor:
    response: ResponseContext[object]

    def extract(self, model: type[object]) -> ParseAttempt[object]:
        parsed = self.response.json
        if parsed.error is not None:
            return Malformed(parsed.error)
        return ParsedValue(parsed.value)


JSON_EXTRACTOR = JsonExtractor()


@dataclass(frozen=True, slots=True)
class HtmlExtractor:
    name: str = "html"

    def bind(self, response: ResponseContext[object]) -> BoundResponseExtractor:
        return _BoundHtmlExtractor(response, self)


@dataclass(frozen=True, slots=True)
class _BoundHtmlExtractor:
    response: ResponseContext[object]
    identity: HtmlExtractor

    def extract(self, model: type[object]) -> ParseAttempt[object]:
        try:
            from eazy_sdk.extraction import HtmlDocument

            document = self.response.cached(
                self.identity, lambda: HtmlDocument(self.response.bytes)
            )
            return ParsedValue(document.extract(model, models=self.response.models))
        except Exception as exc:
            return Malformed(exc)


HTML_EXTRACTOR = HtmlExtractor()


@dataclass(frozen=True, slots=True)
class Json[T]:
    model: type[T]
    media_type: str = "application/json"
    extractor: ResponseExtractor = JSON_EXTRACTOR


@dataclass(frozen=True, slots=True)
class Html[T]:
    model: type[T]
    media_type: str = "text/html"
    extractor: ResponseExtractor = HTML_EXTRACTOR


@dataclass(frozen=True, slots=True)
class Extracted[T]:
    model: type[T]
    using: ResponseExtractor
    media_type: str | None = None

    @property
    def extractor(self) -> ResponseExtractor:
        return self.using


@dataclass(frozen=True, slots=True)
class Parsed[T]:
    model: type[T]
    using: ResponseParser
    media_type: str | None = None

    @property
    def parser(self) -> ResponseParser:
        return self.using


@dataclass(frozen=True, slots=True)
class Text:
    media_type: str | None = "text/plain"


@dataclass(frozen=True, slots=True)
class Bytes:
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class Empty:
    media_type: str | None = None


type ResponseRepresentation[T] = Json[T] | Html[T] | Extracted[T] | Parsed[T] | Text | Bytes | Empty
type ResponseCondition = Callable[[ResponseContext[object]], bool]


@dataclass(frozen=True, slots=True)
class StatusRange:
    start: int
    end: int

    def matches(self, status: int) -> bool:
        return self.start <= status <= self.end


@dataclass(frozen=True, slots=True)
class DefaultStatus:
    pass


DEFAULT = DefaultStatus()
type StatusSelector = int | StatusRange | DefaultStatus


class ApiError[T](Exception):
    def __init__(self, error: T, context: ResponseContext[object]) -> None:
        self.error = error
        self.context = context
        super().__init__(f"documented API error for {context.operation.operation_id}")


type ApiErrorFactory[T] = (
    type[ApiError[typing.Any]] | Callable[[T, ResponseContext[object]], Exception]
)


@dataclass(frozen=True, slots=True, eq=False)
class Success[T]:
    status: StatusSelector
    response: ResponseRepresentation[T]
    condition: ResponseCondition | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Error[T]:
    status: StatusSelector
    response: ResponseRepresentation[T]
    exception: ApiErrorFactory[T] = ApiError
    condition: ResponseCondition | None = None


type ResponseCase[T] = Success[T] | Error[T]


@dataclass(frozen=True, slots=True)
class SuccessOutcome[T]:
    value: T
    case: Success[T]
    context: ResponseContext[object]

    def unwrap(self) -> T:
        return self.value


@dataclass(frozen=True, slots=True)
class ErrorOutcome[T]:
    error: T
    case: Error[T]
    context: ResponseContext[object]

    def unwrap(self) -> None:
        raise self.case.exception(self.error, self.context)


@dataclass(frozen=True, slots=True)
class UnexpectedOutcome:
    attempted_models: tuple[str, ...]
    context: ResponseContext[object]

    def unwrap(self) -> None:
        raise UnexpectedResponseError(self)


@dataclass(frozen=True, slots=True)
class MalformedOutcome:
    case: ResponseCase[object]
    parser: ResponseParser | ResponseExtractor | None
    cause: Exception
    context: ResponseContext[object]

    def unwrap(self) -> None:
        raise MalformedResponseError(self) from self.cause


@dataclass(frozen=True, slots=True)
class AmbiguousResponseOutcome:
    cases: tuple[ResponseCase[object], ...]
    context: ResponseContext[object]

    def unwrap(self) -> None:
        raise AmbiguousResponseError(self)


class UnexpectedResponseError(Exception):
    pass


class MalformedResponseError(Exception):
    pass


class AmbiguousResponseError(Exception):
    pass


type ResponseOutcome[T] = (
    SuccessOutcome[T]
    | ErrorOutcome[object]
    | UnexpectedOutcome
    | MalformedOutcome
    | AmbiguousResponseOutcome
)


@dataclass(frozen=True, slots=True)
class ResponseEnvelope[T, TRaw = object]:
    value: T
    response: NormalizedResponse[TRaw]

    @property
    def status_code(self) -> int:
        return self.response.status_code

    @property
    def headers(self) -> Headers:
        return cast_headers(self.response.headers)


@dataclass(frozen=True, slots=True, init=False)
class Responses[T]:
    success: tuple[Success[object], ...]
    errors: tuple[Error[object], ...]
    fallback: Error[object] | None

    def __init__(
        self,
        *,
        success: tuple[Success[T], ...] | Mapping[int, ResponseRepresentation[T]],
        errors: tuple[Error[typing.Any], ...] | Mapping[int, Error[typing.Any]] = (),
        fallback: Error[object] | None = None,
    ) -> None:
        normalized_success = (
            tuple(Success(status, representation) for status, representation in success.items())
            if isinstance(success, Mapping)
            else success
        )
        normalized_errors = tuple(errors.values()) if isinstance(errors, Mapping) else errors
        object.__setattr__(
            self,
            "success",
            typing.cast(tuple[Success[object], ...], normalized_success),
        )
        object.__setattr__(self, "errors", normalized_errors)
        object.__setattr__(self, "fallback", fallback)

    @property
    def _result_type(self) -> object | None:
        """Return the successful runtime type encoded by the response representations."""
        result_types = tuple(
            dict.fromkeys(_representation_result_type(case.response) for case in self.success)
        )
        if not result_types:
            return None
        if len(result_types) == 1:
            return result_types[0]
        try:
            return reduce(operator.or_, result_types)
        except TypeError:
            return None

    @property
    def cases(self) -> tuple[ResponseCase[object], ...]:
        return (*self.success, *self.errors)

    def inspect(self, context: ResponseContext[object]) -> ResponseOutcome[T]:
        candidates = [
            case
            for case in self.cases
            if _status_matches(case.status, context.response.status_code)
            and _media_matches(case.response, context.response.content_type)
            and (case.condition is None or case.condition(context))
        ]
        if not candidates and self.fallback is not None:
            candidates = [self.fallback]
        if not candidates:
            return UnexpectedOutcome((), context)
        matches: list[tuple[ResponseCase[object], object]] = []
        malformed: list[tuple[ResponseCase[object], Malformed]] = []
        attempted: list[str] = []
        parser_sessions: dict[int, BoundResponseParser] = {}
        extractor_sessions: dict[int, BoundResponseExtractor] = {}
        for case in candidates:
            representation = case.response
            if isinstance(representation, Text):
                matches.append((case, context.response.text()))
                continue
            if isinstance(representation, Bytes):
                matches.append((case, context.bytes))
                continue
            if isinstance(representation, Empty):
                if context.bytes:
                    malformed.append((case, Malformed(ValueError("expected empty body"))))
                else:
                    matches.append((case, None))
                continue
            attempted.append(representation.model.__name__)
            decoder: ResponseParser | ResponseExtractor
            if isinstance(representation, Json | Html | Extracted):
                extractor = representation.extractor
                extractor_session = extractor_sessions.get(id(extractor))
                if extractor_session is None:
                    extractor_session = extractor.bind(context)
                    extractor_sessions[id(extractor)] = extractor_session
                primitive = extractor_session.extract(representation.model)
                if isinstance(primitive, ParsedValue):
                    try:
                        sourced = _apply_header_sources(
                            representation.model,
                            primitive.value,
                            context.headers,
                            context.models,
                        )
                        result: ParseAttempt[object] = ParsedValue(
                            context.models.load(representation.model, sourced)
                        )
                    except Exception as exc:
                        result = Malformed(exc)
                else:
                    result = primitive
                decoder = extractor
            else:
                parser = representation.parser
                parser_session = parser_sessions.get(id(parser))
                if parser_session is None:
                    parser_session = parser.bind(context)
                    parser_sessions[id(parser)] = parser_session
                result = parser_session.try_parse(representation.model)
                decoder = parser
            if isinstance(result, ParsedValue):
                matches.append((case, result.value))
            elif isinstance(result, Malformed):
                malformed.append((case, Malformed(result.cause, decoder)))
        arbitration = arbitrate_cases(matches, malformed)
        if isinstance(arbitration, AmbiguousCases):
            return AmbiguousResponseOutcome(arbitration.cases, context)
        if isinstance(arbitration, SelectedCase):
            case, value = arbitration.case, arbitration.value
            if isinstance(case, Success):
                return cast(ResponseOutcome[T], SuccessOutcome(value, case, context))
            return ErrorOutcome(value, case, context)
        if isinstance(arbitration, MalformedCase):
            malformed_parser = cast(
                ResponseParser | ResponseExtractor | None,
                arbitration.malformed.details,
            )
            return MalformedOutcome(
                arbitration.case,
                malformed_parser,
                arbitration.malformed.cause,
                context,
            )
        assert isinstance(arbitration, NoCaseMatch)
        return UnexpectedOutcome(tuple(attempted), context)


def JsonResponse[T](model: type[T]) -> Json[T]:
    return Json(model)


def _representation_result_type(representation: ResponseRepresentation[object]) -> object:
    if isinstance(representation, Text):
        return str
    if isinstance(representation, Bytes):
        return bytes
    if isinstance(representation, Empty):
        return type(None)
    return representation.model


def _status_matches(selector: StatusSelector, status: int) -> bool:
    if isinstance(selector, int):
        return selector == status
    if isinstance(selector, StatusRange):
        return selector.matches(status)
    return True


def _media_matches(representation: ResponseRepresentation[object], actual: str | None) -> bool:
    expected = representation.media_type
    if expected is None:
        return True
    if actual is None:
        return False
    expected_type, expected_subtype = expected.lower().split("/", 1)
    actual_type, actual_subtype = actual.lower().split("/", 1)
    return (expected_type == "*" or expected_type == actual_type) and (
        expected_subtype == "*"
        or expected_subtype == actual_subtype
        or (expected_subtype.endswith("+json") and actual_subtype.endswith("+json"))
    )
