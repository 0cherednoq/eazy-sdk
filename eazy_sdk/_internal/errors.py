"""Configuration and runtime errors for the compiled execution path."""

from __future__ import annotations


class PlanError(Exception):
    """A contract or runtime registry could not be compiled safely."""


class BindingError(PlanError):
    """Call arguments do not belong to the compiled request shape."""


class PatchError(PlanError):
    """A request patch cannot be validated or committed."""


class GraphError(PlanError):
    """A compiled plan graph is cyclic or violates phase ordering."""


class WriterConflictError(GraphError):
    """Multiple plan nodes write the same slot without an explicit policy."""
