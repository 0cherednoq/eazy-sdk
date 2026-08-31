"""Sentinel for distinguishing an omitted argument from an explicit value."""

from __future__ import annotations

from typing import Final


class Unset:
    """Singleton marker meaning an argument was not provided."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final[Unset] = Unset()
