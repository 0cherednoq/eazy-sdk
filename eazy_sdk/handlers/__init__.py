"""Zapros handler extension boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .profile import (
    CONSERVATIVE_HANDLER_PROFILE,
    AutomaticHeaderPolicy,
    CapabilityLevel,
    CapabilityMismatchError,
    CaptureEvidence,
    EmitOptions,
    HandlerProfile,
    RedirectControl,
    TransportError,
    validate_profile,
)

if TYPE_CHECKING:
    from .zapros import (
        BorrowedAsyncHandler,
        BorrowedHandler,
        ZaprosAsyncEmitter,
        ZaprosSyncEmitter,
    )


def __getattr__(name: str) -> Any:
    if name not in {
        "BorrowedAsyncHandler",
        "BorrowedHandler",
        "ZaprosAsyncEmitter",
        "ZaprosSyncEmitter",
    }:
        raise AttributeError(name)
    from .zapros import (
        BorrowedAsyncHandler,
        BorrowedHandler,
        ZaprosAsyncEmitter,
        ZaprosSyncEmitter,
    )

    values = {
        "BorrowedAsyncHandler": BorrowedAsyncHandler,
        "BorrowedHandler": BorrowedHandler,
        "ZaprosAsyncEmitter": ZaprosAsyncEmitter,
        "ZaprosSyncEmitter": ZaprosSyncEmitter,
    }
    value = values[name]
    globals()[name] = value
    return value


__all__ = [
    "CONSERVATIVE_HANDLER_PROFILE",
    "AutomaticHeaderPolicy",
    "BorrowedAsyncHandler",
    "BorrowedHandler",
    "CapabilityLevel",
    "CapabilityMismatchError",
    "CaptureEvidence",
    "EmitOptions",
    "HandlerProfile",
    "RedirectControl",
    "TransportError",
    "ZaprosAsyncEmitter",
    "ZaprosSyncEmitter",
    "validate_profile",
]
