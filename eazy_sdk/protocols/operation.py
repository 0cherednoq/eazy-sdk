"""An RPC operation is an HTTP operation whose name travels in the body.

Nothing else about it is different: the same frozen class, the same fields, the same one
descriptor and the same execution path. What ``Rpc.method`` adds is the two things the
envelope owns — the URL comes from the router's ``protocol``, and the cases are read out of
the envelope rather than off the status line.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Unpack

from eazy_sdk.operation import Http, HttpOperation, _HttpOptions, _HttpSpec


class RpcOperation[T](HttpOperation[T]):
    """One call of an RPC service, declared as a frozen model class like any operation."""

    __slots__ = ()


class Rpc:
    """The verb of an RPC service: a method name instead of a path."""

    @staticmethod
    def method(discriminator: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        """``Rpc.method("account.charge", errors={-32001: Fault})``.

        ``errors`` is keyed by the protocol's own error code, not by an HTTP status: an RPC
        service answers ``200`` and says what went wrong inside the envelope. Every other
        option is the one ``Http.post`` takes and means the same thing.
        """

        if not isinstance(discriminator, str) or not discriminator:
            raise TypeError("Rpc.method requires the name the envelope calls this operation")
        spec = Http.request("POST", "/", **options)
        return replace(spec, discriminator=discriminator, envelope_cases=True)


def rpc_responses(
    *,
    result_type: object,
    errors: Mapping[Any, Any] | Sequence[Any],
    fallback: object,
    operation_id: str,
) -> tuple[Any, Any, Any]:
    """Lower an RPC declaration into the ordinary success, error and fallback cases.

    The mapping the operation wrote is by error code; the cases it becomes are the ones
    phase 49 already executes, so the runtime learns nothing new.
    """

    from eazy_sdk.core.errors import PlanError

    from .jsonrpc import rpc_error, rpc_error_default, rpc_result

    if not isinstance(errors, Mapping):
        raise PlanError(
            f"RPC operation {operation_id!r} declares errors= as a sequence; "
            "use a mapping {code: Model}"
        )
    success = (rpc_result(_model(result_type, operation_id)),)
    failures = tuple(
        rpc_error(code, _model(model, operation_id), exception=_exception(model))
        for code, model in errors.items()
    )
    default = (
        rpc_error_default(_model(fallback, operation_id), exception=_exception(fallback))
        if fallback is not None
        else None
    )
    return success, failures, default


def _model(spec: object, operation_id: str) -> Any:
    """The problem model behind an ``ApiError`` subclass, or the model itself."""

    from eazy_sdk.core.errors import PlanError
    from eazy_sdk.response._mapping import problem_model
    from eazy_sdk.response.cases import ApiError

    if isinstance(spec, type) and issubclass(spec, ApiError):
        model = problem_model(spec)
        if model is None:
            raise PlanError(
                f"{spec.__name__} does not name a problem model; declare "
                f"class {spec.__name__}(ApiError[Model]) or use (Model, factory)"
            )
        return model
    if not isinstance(spec, type):
        raise PlanError(f"RPC operation {operation_id!r} declares a non-model case {spec!r}")
    return spec


def _exception(spec: object) -> Any:
    from eazy_sdk.response.cases import ApiError

    if isinstance(spec, type) and issubclass(spec, ApiError):
        return spec
    return None


__all__ = ["Rpc", "RpcOperation", "rpc_responses"]
