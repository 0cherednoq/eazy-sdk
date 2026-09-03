"""Lifecycle graph shared by auth resolution and the clients (no accounts dependency)."""

from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk.core.errors import EazySdkError


class LifecycleCycleError(EazySdkError, RuntimeError):
    """A lifecycle node re-entered itself while resolving a session or dependency."""


@dataclass(frozen=True, slots=True)
class LifecycleNode:
    lifecycle_identity: int
    operation: str
    diagnostic_name: str


@dataclass(frozen=True, slots=True)
class LifecycleGraph:
    """Explicit parent chain shared by any transport-specific lifecycle context."""

    active: tuple[LifecycleNode, ...] = ()

    def enter(self, node: LifecycleNode) -> LifecycleGraph:
        if any(item.lifecycle_identity == node.lifecycle_identity for item in self.active):
            names = [item.diagnostic_name for item in (*self.active, node)]
            raise LifecycleCycleError("lifecycle cycle: " + " -> ".join(names))
        return LifecycleGraph((*self.active, node))


__all__ = ["LifecycleCycleError", "LifecycleGraph", "LifecycleNode"]
