"""Visible-text extraction. The stdlib implementation is not browser-perfect."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Protocol, runtime_checkable

_SKIP_TAGS = frozenset({"script", "style", "head", "meta", "noscript", "template", "svg"})
_HIDDEN_STYLE = re.compile(
    r"(display\s*:\s*none\b"
    r"|visibility\s*:\s*hidden\b"
    r"|opacity\s*:\s*0(?!\.\d*[1-9]))",
    re.IGNORECASE,
)
_HTML5_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_WS = re.compile(r"\s+")


@runtime_checkable
class VisibleTextExtractor(Protocol):
    """Extracts human-visible text from an HTML string."""

    def extract(self, html: str) -> str: ...


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth > 0:
            # Inside a skipped subtree: only non-void tags will get a matching
            # end tag, so only they may increment the depth counter.
            if tag not in _HTML5_VOID:
                self._skip_depth += 1
            return
        if tag in _HTML5_VOID:
            # Void elements have no subtree / no text content; nothing to skip.
            return
        if tag in _SKIP_TAGS or self._is_hidden(attrs):
            self._skip_depth = 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tags (<br/>) have no subtree and carry no visible text.
        # Do nothing so they never perturb the skip-depth counter.
        return

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    @staticmethod
    def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
        for name, value in attrs:
            if name == "hidden":
                return True
            if name == "style" and value and _HIDDEN_STYLE.search(value):
                return True
        return False

    def result(self) -> str:
        return _WS.sub(" ", " ".join(self._chunks)).strip()


class StdlibVisibleTextExtractor:
    """Dependency-free visible-text extractor built on ``html.parser``.

    It ignores ``script``/``style``/``head``/``noscript``/``template``/``svg``,
    elements marked ``hidden``, and inline ``display:none`` /
    ``visibility:hidden`` / ``opacity:0``. It does not compute real CSS layout.
    """

    def extract(self, html: str) -> str:
        parser = _VisibleTextParser()
        parser.feed(html)
        parser.close()
        return parser.result()
