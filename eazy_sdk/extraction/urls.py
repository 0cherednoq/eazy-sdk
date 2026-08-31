"""URL and redirect extraction models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

UrlSource = Literal[
    "a.href",
    "form.action",
    "script.src",
    "link.href",
    "img.src",
    "iframe.src",
    "meta_refresh",
    "raw_html",
]


@dataclass(frozen=True)
class ExtractedUrl:
    """A URL found in an HTML document."""

    url: str
    raw_url: str
    source: UrlSource
    tag: str | None = None
    attr: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class HtmlRedirect:
    """A redirect derived from HTML (e.g. ``<meta http-equiv=refresh>``)."""

    url: str
    raw_url: str
    delay: float | None = None
    source: Literal["meta_refresh"] = "meta_refresh"
