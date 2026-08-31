"""Optional HTTP interaction context for the transport-neutral account lifecycle.

Importing :mod:`eazy_sdk.accounts` does not import this adapter. It is downstream from the
lifecycle core and exposes a scoped SDK to account services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eazy_sdk.response import ResponseEnvelope


@dataclass(slots=True)
class HttpRegistrationContext[TSdk]:
    sdk: TSdk
    _responses: list[object] = field(default_factory=list, init=False, repr=False)

    def capture[T](self, envelope: ResponseEnvelope[T, Any]) -> T:
        """Record an SDK response used by a lifecycle protocol and return its value."""

        self._responses.append(envelope.response)
        return envelope.value

    @property
    def responses(self) -> tuple[object, ...]:
        return tuple(self._responses)


@dataclass(slots=True)
class SyncHttpRegistrationContext[TSdk](HttpRegistrationContext[TSdk]):
    """Account context for a sync facade's async lifecycle runner."""


__all__ = ["HttpRegistrationContext", "SyncHttpRegistrationContext"]
