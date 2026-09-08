"""Composable predicates over a raw response, for ``when=`` on any case.

::

    from eazy_sdk.response.match import body, content_type

    is_pdf = body.startswith(b"%PDF-")
    is_challenge = body.contains(b"captcha.execute") | body.contains(b'name="token"')
    is_regular = ~is_challenge & content_type.startswith("text/html")

Every factory here returns a :class:`Predicate`, which is an ordinary callable satisfying
``ResponseCondition``. It is sugar over the existing type, never a second one: a plain function
or lambda works wherever a predicate does, and the two mix freely
(``is_pdf & (lambda ctx: ctx.response.status_code == 200)``).

A ``bytes`` argument is compared against the raw body, a ``str`` against the decoded text, and a
comparison against text that does not decode is ``False`` rather than an error. That way a body
in a non-UTF-8 encoding cannot turn a routing decision into a crashed call, and an author writing
a Cyrillic marker does not have to reach for ``.encode()`` or guess the charset.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eazy_sdk.response.cases import ResponseContext

__all__ = ["Predicate", "body", "content_type", "header", "status"]


@dataclass(frozen=True, slots=True)
class Predicate:
    """One decision about a response, combinable with ``&``, ``|`` and ``~``.

    ``label`` is what the predicate reads as in a diagnostic, so an ambiguous or unexpected
    response names the criteria that were weighed rather than a list of ``<lambda>``.
    """

    test: Callable[[ResponseContext[object]], bool]
    label: str

    def __call__(self, context: ResponseContext[object]) -> bool:
        return self.test(context)

    def __and__(self, other: Callable[[ResponseContext[object]], bool]) -> Predicate:
        return Predicate(
            lambda context: self.test(context) and bool(other(context)),
            f"({self.label} and {_label_of(other)})",
        )

    def __or__(self, other: Callable[[ResponseContext[object]], bool]) -> Predicate:
        return Predicate(
            lambda context: self.test(context) or bool(other(context)),
            f"({self.label} or {_label_of(other)})",
        )

    def __invert__(self) -> Predicate:
        return Predicate(lambda context: not self.test(context), f"not {self.label}")

    def __repr__(self) -> str:
        return f"<{self.label}>"


def _label_of(other: object) -> str:
    if isinstance(other, Predicate):
        return other.label
    return getattr(other, "__name__", type(other).__name__)


def _text_of(context: ResponseContext[object]) -> str | None:
    """The decoded body, or ``None`` when it does not decode; never an exception."""

    parsed = context.text
    return None if parsed.error is not None else parsed.value


def _media_of(context: ResponseContext[object]) -> str:
    """The content type without its parameters, lowercased."""

    declared = context.response.content_type or ""
    return declared.split(";", 1)[0].strip().lower()


class _Body:
    """Predicates over the response body. Use the module-level ``body``."""

    __slots__ = ()

    def startswith(self, prefix: bytes | str) -> Predicate:
        if isinstance(prefix, bytes):
            return Predicate(
                lambda context: context.bytes.startswith(prefix), f"body startswith {prefix!r}"
            )
        return Predicate(
            lambda context: (text := _text_of(context)) is not None and text.startswith(prefix),
            f"body startswith {prefix!r}",
        )

    def contains(self, marker: bytes | str, *, ignore_case: bool = False) -> Predicate:
        label = f"body contains {marker!r}" + (" ignoring case" if ignore_case else "")
        if isinstance(marker, bytes):
            needle = marker.lower() if ignore_case else marker

            def in_bytes(context: ResponseContext[object]) -> bool:
                haystack = context.bytes.lower() if ignore_case else context.bytes
                return needle in haystack

            return Predicate(in_bytes, label)

        text_needle = marker.lower() if ignore_case else marker

        def in_text(context: ResponseContext[object]) -> bool:
            text = _text_of(context)
            if text is None:
                return False
            return text_needle in (text.lower() if ignore_case else text)

        return Predicate(in_text, label)

    def matches(self, pattern: re.Pattern[bytes] | re.Pattern[str]) -> Predicate:
        """A compiled pattern; a bytes pattern searches the body, a str one the decoded text."""

        label = f"body matches {pattern.pattern!r}"
        if isinstance(pattern.pattern, bytes):
            raw: re.Pattern[bytes] = pattern
            return Predicate(lambda context: raw.search(context.bytes) is not None, label)
        decoded: re.Pattern[str] = pattern
        return Predicate(
            lambda context: (
                (text := _text_of(context)) is not None and decoded.search(text) is not None
            ),
            label,
        )

    def is_empty(self) -> Predicate:
        return Predicate(lambda context: not context.bytes, "body is empty")


class _ContentType:
    """Predicates over the response content type, parameters stripped."""

    __slots__ = ()

    def is_(self, media_type: str) -> Predicate:
        expected = media_type.lower()
        return Predicate(
            lambda context: _media_of(context) == expected, f"content type is {media_type!r}"
        )

    def startswith(self, prefix: str) -> Predicate:
        expected = prefix.lower()
        return Predicate(
            lambda context: _media_of(context).startswith(expected),
            f"content type startswith {prefix!r}",
        )


class _Status:
    """Predicates over the response status code."""

    __slots__ = ()

    def is_(self, code: int) -> Predicate:
        return Predicate(lambda context: context.response.status_code == code, f"status is {code}")

    def in_(self, start: int, end: int) -> Predicate:
        return Predicate(
            lambda context: start <= context.response.status_code <= end,
            f"status in {start}..{end}",
        )


@dataclass(frozen=True, slots=True)
class header:  # a factory, spelled lowercase because it reads as a call site
    """Predicates over one response header, matched case-insensitively by name."""

    name: str

    def _value(self, context: ResponseContext[object]) -> str | None:
        return context.headers.get(self.name)

    def present(self) -> Predicate:
        return Predicate(
            lambda context: self._value(context) is not None, f"header {self.name!r} is present"
        )

    def is_(self, value: str) -> Predicate:
        return Predicate(
            lambda context: self._value(context) == value,
            f"header {self.name!r} is {value!r}",
        )

    def contains(self, marker: str) -> Predicate:
        return Predicate(
            lambda context: (found := self._value(context)) is not None and marker in found,
            f"header {self.name!r} contains {marker!r}",
        )


body = _Body()
content_type = _ContentType()
status = _Status()
