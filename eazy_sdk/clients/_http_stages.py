"""Typed, effect-free stages used by the single HTTP execution coordinator."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import urljoin

from eazy_sdk._internal import (
    OperationBindingError,
    OperationValues,
    ValuePatch,
    WriterConflictError,
)
from eazy_sdk._internal.http_compiler import CompiledContract
from eazy_sdk.clients.base import RedirectLimitExceeded, UnsafeReplayError
from eazy_sdk.middleware import RedirectTo, RetryAttempt
from eazy_sdk.models import ModelAdapterError, ModelAdapterRegistry, ModelDumpMode
from eazy_sdk.protection import SignalMatch, SignalOutcome
from eazy_sdk.request import MultipartBody
from eazy_sdk.request.prepared import _NO_BODY_DOCUMENT_OVERRIDE
from eazy_sdk.response import NormalizedResponse
from eazy_sdk.response.cases import ResponseOutcome, SuccessOutcome
from eazy_sdk.response.normalized import cast_headers


@dataclass(frozen=True, slots=True)
class RequestDocumentStageInput[T]:
    compiled: CompiledContract[T]
    values: OperationValues
    models: ModelAdapterRegistry
    private_values: Mapping[int, object]


@dataclass(frozen=True, slots=True)
class RequestDocumentStageOutput:
    document: object


def build_request_document[T](
    stage: RequestDocumentStageInput[T],
) -> RequestDocumentStageOutput:
    """Build one fresh semantic request document for the current attempt."""

    compiled = stage.compiled
    if compiled.body_projection is not None:
        document = _project_body(
            compiled,
            stage.values,
            stage.models,
            stage.private_values,
        )
    elif compiled.private_body_writers:
        document = _managed_body_document(compiled, stage.values, stage.models)
    else:
        document = _NO_BODY_DOCUMENT_OVERRIDE
    return RequestDocumentStageOutput(document)


@dataclass(frozen=True, slots=True)
class ResponseDecisionInput[T]:
    response: NormalizedResponse[object]
    proposed: object | None
    signal: SignalOutcome
    outcome: ResponseOutcome[T] | None
    idempotent: bool
    attempt: int
    hard_attempt_limit: int
    transport_remaining: int
    retry_statuses: frozenset[int]
    redirect_remaining: int
    auth_remaining: int
    auth_refreshable: bool
    current_url: str
    effective_method: str
    raw_response: bool


@dataclass(frozen=True, slots=True)
class RetryTransition:
    kind: str
    patch: ValuePatch | None = None
    consumes_transport: bool = False


@dataclass(frozen=True, slots=True)
class RedirectTransition:
    url: str
    method: str | None = None
    omit_body: bool = False


@dataclass(frozen=True, slots=True)
class ReactionTransition:
    match: SignalMatch[object]


@dataclass(frozen=True, slots=True)
class AuthRefreshTransition:
    pass


@dataclass(frozen=True, slots=True)
class TerminalResponse[T]:
    value: T
    response: NormalizedResponse[object]


@dataclass(frozen=True, slots=True)
class RejectedResponse:
    outcome: ResponseOutcome[object]


type ResponseDecision[T] = (
    RetryTransition
    | RedirectTransition
    | ReactionTransition
    | AuthRefreshTransition
    | TerminalResponse[T]
    | RejectedResponse
)


def decide_response[T](stage: ResponseDecisionInput[T]) -> ResponseDecision[T]:
    """Select exactly one transition or terminal result without executing effects."""

    proposed = stage.proposed
    if isinstance(proposed, RetryAttempt):
        _require_idempotent(stage.idempotent, "middleware replay")
        return RetryTransition(proposed.kind, proposed.patch)
    if isinstance(proposed, RedirectTo):
        _require_redirect_budget(stage.redirect_remaining)
        return RedirectTransition(urljoin(stage.current_url, proposed.url))
    if (
        stage.response.status_code in stage.retry_statuses
        and stage.transport_remaining > 0
        and stage.attempt < stage.hard_attempt_limit
    ):
        _require_idempotent(stage.idempotent, "retry policy")
        return RetryTransition("response-retry", consumes_transport=True)
    if isinstance(stage.signal, SignalMatch):
        return ReactionTransition(stage.signal)
    if (
        stage.response.status_code == 401
        and stage.auth_remaining > 0
        and stage.attempt < stage.hard_attempt_limit
        and stage.auth_refreshable
    ):
        _require_idempotent(stage.idempotent, "auth refresh")
        return AuthRefreshTransition()
    location = cast_headers(stage.response.headers).get("location")
    if stage.response.status_code in {301, 302, 303, 307, 308} and location is not None:
        _require_redirect_budget(stage.redirect_remaining)
        method: str | None = None
        omit_body = False
        if (stage.response.status_code == 303 and stage.effective_method != "HEAD") or (
            stage.response.status_code in {301, 302} and stage.effective_method == "POST"
        ):
            method = "GET"
            omit_body = True
        return RedirectTransition(
            urljoin(stage.response.url or stage.current_url, location),
            method,
            omit_body,
        )
    if stage.raw_response:
        return TerminalResponse(cast(T, stage.response), stage.response)
    outcome = stage.outcome
    if isinstance(outcome, SuccessOutcome):
        return TerminalResponse(outcome.value, stage.response)
    assert outcome is not None
    return RejectedResponse(outcome)
def _require_idempotent(idempotent: bool, source: str) -> None:
    if not idempotent:
        raise UnsafeReplayError(f"{source} requires an idempotent operation")


def _require_redirect_budget(remaining: int) -> None:
    if remaining <= 0:
        raise RedirectLimitExceeded("redirect budget exhausted")


def _project_body[T](
    compiled: CompiledContract[T],
    values: OperationValues,
    models: ModelAdapterRegistry,
    private_values: Mapping[int, object],
) -> object:
    projection = compiled.body_projection
    assert projection is not None
    source = {
        name: copy.deepcopy(values.require(slot))
        for name, slot in compiled.projection_slots.items()
        if values.contains(slot)
    }
    try:
        projected = projection.using(source)
    except Exception:
        raise OperationBindingError(
            code="projection_failed",
            operation_id=compiled.contract.operation_id,
            field=None,
            phase="projection",
            detail=f"body projection {projection.fingerprint_name!r} failed",
        ) from None
    mode: ModelDumpMode = "python" if isinstance(projection.encoding, MultipartBody) else "json"
    try:
        if compiled.private_wire_writers or compiled.private_body_writers:
            document = models.dump(projected, mode=mode)
            if not isinstance(document, Mapping):
                raise ModelAdapterError("projected target must produce an object document")
            selected = tuple(
                (
                    writer,
                    _select_protection_field(
                        private_values[id(writer.requirement)],
                        writer.result_field,
                    ),
                )
                for writer in compiled.private_wire_writers
            )
            document = copy.deepcopy(dict(document))
            for writer, private_value in selected:
                _write_private_wire_value(
                    document,
                    writer.validation_path,
                    private_value,
                    wire_path=writer.path,
                )
            for body_writer in compiled.private_body_writers:
                if values.contains(body_writer.slot):
                    _write_private_wire_value(
                        document,
                        body_writer.path,
                        values.require(body_writer.slot),
                        wire_path=body_writer.path,
                    )
            projected = document
        validated = models.load(projection.target, projected)
        document = models.dump(validated, mode=mode)
        for path in compiled.body_signature_paths:
            if isinstance(document, Mapping) and _wire_path_present(document, path):
                raise WriterConflictError(
                    "body projection and signature output collide at "
                    f"{'.'.join(path)!r}"
                )
        return document
    except WriterConflictError:
        raise
    except Exception as exc:
        validation_field = _projection_validation_path(exc)
        raise OperationBindingError(
            code="projection_target_invalid",
            operation_id=compiled.contract.operation_id,
            field=validation_field,
            phase="projection",
            detail=f"body projection {projection.fingerprint_name!r} target validation failed",
        ) from None


def _managed_body_document[T](
    compiled: CompiledContract[T],
    values: OperationValues,
    models: ModelAdapterRegistry,
) -> object:
    if compiled.body_slot is not None:
        document = models.dump(values.require(compiled.body_slot), mode="json")
    else:
        document = {
            name: copy.deepcopy(values.require(slot))
            for name, slot in compiled.body_field_slots.items()
            if values.contains(slot)
        }
    if not isinstance(document, Mapping):
        raise OperationBindingError(
            code="private_body_invalid",
            operation_id=compiled.contract.operation_id,
            field=None,
            phase="projection",
            detail="managed private body bindings require an object document",
        )
    output = copy.deepcopy(dict(document))
    for writer in compiled.private_body_writers:
        if values.contains(writer.slot):
            _write_private_wire_value(
                output,
                writer.path,
                values.require(writer.slot),
                wire_path=writer.path,
            )
    return output


def _write_private_wire_value(
    document: dict[str, object],
    path: tuple[str, ...],
    value: object,
    *,
    wire_path: tuple[str, ...],
) -> None:
    if wire_path != path and _wire_path_present(document, wire_path):
        raise WriterConflictError(
            "body projection and private wire writer collide at "
            f"{'.'.join(wire_path)!r}"
        )
    current = document
    for component in path[:-1]:
        nested = current.get(component)
        if nested is None:
            created: dict[str, object] = {}
            current[component] = created
            current = created
            continue
        if not isinstance(nested, Mapping):
            raise WriterConflictError(
                "private wire writer cannot traverse occupied path "
                f"{'.'.join(wire_path)!r}"
            )
        copied = dict(nested)
        current[component] = copied
        current = copied
    terminal = path[-1]
    if terminal in current:
        raise WriterConflictError(
            "body projection and private wire writer collide at "
            f"{'.'.join(wire_path)!r}"
        )
    current[terminal] = copy.deepcopy(value)


def _wire_path_present(document: Mapping[str, object], path: tuple[str, ...]) -> bool:
    current: object = document
    for component in path:
        if not isinstance(current, Mapping) or component not in current:
            return False
        current = current[component]
    return True


def _projection_validation_path(error: Exception) -> str | None:
    errors = getattr(error, "errors", None)
    if callable(errors):
        try:
            entries = errors()
        except Exception:
            entries = ()
        if entries and isinstance(entries[0], Mapping):
            location = entries[0].get("loc")
            if isinstance(location, tuple | list):
                return ".".join(str(item) for item in location)
    if isinstance(error, ModelAdapterError):
        message = str(error)
        marker = "missing required field "
        if marker in message:
            return message.partition(marker)[2]
    return None


def _select_protection_field(result: object, field_name: str) -> object:
    if isinstance(result, Mapping):
        try:
            return result[field_name]
        except KeyError as exc:
            raise TypeError(f"protection result field is missing: {field_name}") from exc
    try:
        return getattr(result, field_name)
    except AttributeError as exc:
        raise TypeError(f"protection result field is missing: {field_name}") from exc


__all__: list[str] = []
