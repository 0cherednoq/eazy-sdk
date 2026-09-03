"""Configuration and runtime errors for the compiled execution path."""

from __future__ import annotations

from typing import Literal


class EazySdkError(Exception):
    """Base class of every exception raised by Eazy SDK."""


class ConfigurationError(EazySdkError):
    """Base class of configuration errors raised before any transport I/O."""


class PlanError(EazySdkError):
    """A contract or runtime registry could not be compiled safely."""


class BindingError(PlanError):
    """Call arguments do not belong to the compiled request shape."""


type OperationBindingPhase = Literal["bind", "projection", "prepare"]


class OperationBindingError(BindingError):
    """A caller-visible operation value could not be bound or projected safely."""

    __slots__ = ("_debug_slot", "code", "field", "operation_id", "phase")

    def __init__(
        self,
        *,
        code: str,
        operation_id: str,
        field: str | None,
        phase: OperationBindingPhase,
        detail: str,
        debug_slot: str | None = None,
    ) -> None:
        if not code:
            raise ValueError("operation binding error code must not be empty")
        if not operation_id:
            raise ValueError("operation binding error operation_id must not be empty")
        if not detail:
            raise ValueError("operation binding error detail must not be empty")
        message = f"{detail} for operation {operation_id!r} during {phase}"
        if field is not None:
            message += f" at field {field!r}"
        super().__init__(message)
        self.code = code
        self.operation_id = operation_id
        self.field = field
        self.phase = phase
        self._debug_slot = debug_slot

    def as_dict(self) -> dict[str, str | None]:
        """Return the stable, secret-free diagnostics contract."""

        return {
            "code": self.code,
            "operation_id": self.operation_id,
            "field": self.field,
            "phase": self.phase,
        }


class SlotBindingError(BindingError):
    """Internal binding failure retaining slot identity for public error lowering."""

    __slots__ = ("reason", "slot")

    def __init__(self, message: str, *, slot: object, reason: str) -> None:
        super().__init__(message)
        self.slot = slot
        self.reason = reason


class SlotValueError(TypeError):
    """Internal value failure retaining slot identity and an optional nested path."""

    __slots__ = ("path", "slot")

    def __init__(self, message: str, *, slot: object, path: str | None = None) -> None:
        super().__init__(message)
        self.slot = slot
        self.path = path


class PatchError(PlanError):
    """A request patch cannot be validated or committed."""


class GraphError(PlanError):
    """A compiled plan graph is cyclic or violates phase ordering."""


class WriterConflictError(GraphError):
    """Multiple plan nodes write the same slot without an explicit policy."""
