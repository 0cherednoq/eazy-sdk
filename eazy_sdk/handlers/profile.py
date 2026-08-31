"""Read-only transport evidence attached to a Zapros handler."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from eazy_sdk._internal.http_plan import WireRequirements
from eazy_sdk.request.prepared import HttpProtocol


class CapabilityLevel(Enum):
    UNSUPPORTED = 0
    BEST_EFFORT = 1
    CAPTURE_VERIFIED = 2


class AutomaticHeaderPolicy(Enum):
    DISABLED = "disabled"
    MATERIALIZED = "materialized"
    TRANSPORT_CONTROLLED = "transport-controlled"


class RedirectControl(Enum):
    FORCED_OFF = "forced-off"
    CONFIGURABLE = "configurable"
    UNCONTROLLED = "uncontrolled"


@dataclass(frozen=True, slots=True)
class CaptureEvidence:
    transport: str
    version: str
    protocol: HttpProtocol
    mode: str
    fixture: str


@dataclass(frozen=True, slots=True)
class HandlerProfile:
    protocols: frozenset[HttpProtocol]
    exact_target: CapabilityLevel = CapabilityLevel.BEST_EFFORT
    header_order: CapabilityLevel = CapabilityLevel.BEST_EFFORT
    header_casing: CapabilityLevel = CapabilityLevel.BEST_EFFORT
    duplicate_headers: CapabilityLevel = CapabilityLevel.BEST_EFFORT
    preencoded_body: CapabilityLevel = CapabilityLevel.BEST_EFFORT
    manual_cookie_field: CapabilityLevel = CapabilityLevel.BEST_EFFORT
    automatic_headers: AutomaticHeaderPolicy = AutomaticHeaderPolicy.MATERIALIZED
    redirects: RedirectControl = RedirectControl.UNCONTROLLED
    replayable_streams: CapabilityLevel = CapabilityLevel.BEST_EFFORT
    evidence: CaptureEvidence | None = None


@dataclass(frozen=True, slots=True)
class EmitOptions:
    timeout: float | None = None
    proxy: str | None = None
    verify_tls: bool | None = None
    stream_response: bool = False


class TransportFailure(Exception):
    def __init__(self, handler: str, phase: str, attempt: int | None, cause: Exception) -> None:
        self.handler = handler
        self.phase = phase
        self.attempt = attempt
        self.cause = cause
        super().__init__(handler, phase, attempt)

    def __str__(self) -> str:
        suffix = f", attempt={self.attempt}" if self.attempt is not None else ""
        return f"transport failure in {self.handler} during {self.phase}{suffix}"


class CapabilityMismatch(Exception):
    def __init__(self, dimensions: tuple[str, ...]) -> None:
        self.dimensions = dimensions
        super().__init__(dimensions)

    def __str__(self) -> str:
        return "handler capability mismatch: " + ", ".join(self.dimensions)


def validate_profile(requirements: WireRequirements, profile: HandlerProfile) -> None:
    failures: list[str] = []
    for requirement in requirements.dimensions:
        if requirement.dimension == "protocol":
            try:
                protocol = HttpProtocol(requirement.minimum)
            except ValueError:
                failures.append(f"protocol={requirement.minimum} (unknown)")
            else:
                if protocol not in profile.protocols:
                    failures.append(f"protocol={requirement.minimum}")
            continue
        available = getattr(profile, requirement.dimension, None)
        if not isinstance(available, CapabilityLevel):
            failures.append(f"{requirement.dimension} (not declared)")
            continue
        required = CapabilityLevel[requirement.minimum]
        if available.value < required.value:
            failures.append(
                f"{requirement.dimension}: requires {required.name}, has {available.name}"
            )
    if failures:
        raise CapabilityMismatch(tuple(failures))


CONSERVATIVE_HANDLER_PROFILE = HandlerProfile(
    protocols=frozenset({HttpProtocol.HTTP_1_1}),
)


__all__ = [
    "CONSERVATIVE_HANDLER_PROFILE",
    "AutomaticHeaderPolicy",
    "CapabilityLevel",
    "CapabilityMismatch",
    "CaptureEvidence",
    "EmitOptions",
    "HandlerProfile",
    "RedirectControl",
    "TransportFailure",
    "validate_profile",
]
