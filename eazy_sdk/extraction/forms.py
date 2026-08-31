"""HTML form extraction models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractedInput:
    """A single form input."""

    name: str
    value: str | None
    type: str | None


@dataclass(frozen=True)
class ExtractedForm:
    """A parsed HTML form."""

    action: str | None
    method: str
    inputs: list[ExtractedInput] = field(default_factory=list)

    def input(self, name: str) -> str | None:
        """Return the value of the first input named ``name``."""
        for item in self.inputs:
            if item.name == name:
                return item.value
        return None
