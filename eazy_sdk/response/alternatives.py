"""A field read from the first of several places that has it.

A service does not always keep a value in one place: a form token sits in the page for one
client and in a cookie for another. The field names both, in the order to look::

    class LoginPage(BaseModel):
        csrf: Annotated[str, Regex(r'"csrf":"([^"]+)"') | FromCookie("act")]

``A | B`` on two source markers is :class:`FirstOf`. The sources are tried left to right and the
first one that yields a value fills the field. A required field none of them fills is a
malformed response, exactly as with a single source; when one of the sources is
``Location.query(...)``, which states which response this is, it is another case instead.
"""

from __future__ import annotations

from typing import ClassVar

from eazy_sdk.core.errors import PlanError

__all__ = ["Alternative", "FirstOf"]


class Alternative:
    """What a source marker inherits to be written as ``A | B``."""

    __slots__ = ()

    states_the_response: ClassVar[bool] = False
    """Whether a miss of this source means "another response" rather than a missing value."""

    def __or__(self, other: object) -> FirstOf:
        return FirstOf(self, other)


class FirstOf:
    """The sources of one field, tried in the order written."""

    __slots__ = ("sources",)

    sources: tuple[Alternative, ...]

    def __init__(self, *sources: object) -> None:
        flat: list[Alternative] = []
        for source in sources:
            if isinstance(source, FirstOf):
                flat.extend(source.sources)
            elif isinstance(source, Alternative):
                flat.append(source)
            else:
                raise PlanError(
                    f"{source!r} cannot be one of several sources of a field: a source is a "
                    "selector, FromHeader, FromCookie or Location.query; a Location(...) "
                    "pattern states which response this is and stands on a field alone"
                )
        if len(flat) < 2:
            raise PlanError("FirstOf(...) takes at least two sources; one source needs no choice")
        object.__setattr__(self, "sources", tuple(flat))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("FirstOf is immutable")

    def __or__(self, other: object) -> FirstOf:
        return FirstOf(self, other)

    @property
    def states_the_response(self) -> bool:
        return any(source.states_the_response for source in self.sources)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FirstOf) and self.sources == other.sources

    def __hash__(self) -> int:
        return hash((FirstOf, self.sources))

    def __repr__(self) -> str:
        return " | ".join(repr(source) for source in self.sources)
