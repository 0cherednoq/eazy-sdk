"""High-level HTML inspector. Read-only; never validates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urljoin

from .exceptions import MissingExtractedValueError
from .forms import ExtractedForm, ExtractedInput
from .html_scanner import StdlibHtmlScanner
from .urls import ExtractedUrl, HtmlRedirect, UrlSource
from .visible_text import StdlibVisibleTextExtractor, VisibleTextExtractor

_SOURCE_BY_TAG_ATTR: dict[tuple[str, str], UrlSource] = {
    ("a", "href"): "a.href",
    ("form", "action"): "form.action",
    ("script", "src"): "script.src",
    ("link", "href"): "link.href",
    ("img", "src"): "img.src",
    ("iframe", "src"): "iframe.src",
}
_REFRESH_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*;\s*url=(.+)\s*", re.IGNORECASE)


class HtmlInspector:
    """Extracts hidden inputs, meta, URLs, forms, redirects and text from HTML."""

    def __init__(
        self,
        html: str,
        *,
        base_url: str | None = None,
        visible_text_extractor: VisibleTextExtractor | None = None,
    ) -> None:
        self._html = html
        self._base_url = base_url
        self._scanner = StdlibHtmlScanner.scan(html)
        self._vte = visible_text_extractor or StdlibVisibleTextExtractor()
        self._visible_text: str | None = None

    def text(self) -> str:
        """Return concatenated text nodes (alias of visible text in MVP)."""
        return self.visible_text()

    def visible_text(self) -> str:
        if self._visible_text is None:
            self._visible_text = self._vte.extract(self._html)
        return self._visible_text

    def hidden_inputs(self) -> Mapping[str, str]:
        out: dict[str, str] = {}
        for attrs in self._scanner.inputs:
            if (attrs.get("type") or "").lower() == "hidden":
                name = attrs.get("name")
                if name is not None:
                    out[name] = attrs.get("value") or ""
        return out

    def hidden_input(
        self, name: str, *, required: bool = False, default: str | None = None
    ) -> str | None:
        value = self.hidden_inputs().get(name)
        if value is None:
            if required:
                raise MissingExtractedValueError(f"Required hidden input {name!r} not found")
            return default
        return value

    def meta_content(
        self,
        *,
        name: str | None = None,
        property: str | None = None,
        required: bool = False,
        default: str | None = None,
    ) -> str | None:
        if name is None and property is None:
            raise ValueError("meta_content requires either name= or property=")
        for attrs in self._scanner.metas:
            matched = (name is not None and (attrs.get("name") or "").lower() == name.lower()) or (
                property is not None and (attrs.get("property") or "").lower() == property.lower()
            )
            if matched:
                value = attrs.get("content")
                if value is not None:
                    return value
                # matched but no content: keep scanning for another match with content
        if required:
            raise MissingExtractedValueError(
                f"Required meta tag (name={name!r}, property={property!r}) not found"
            )
        return default

    def urls(self, *, absolute: bool = True) -> list[ExtractedUrl]:
        out: list[ExtractedUrl] = []
        for link in self._scanner.links:
            raw = link.value
            if not raw:
                continue
            source = _SOURCE_BY_TAG_ATTR.get((link.tag, link.attr))
            if source is None:
                continue
            url = self._resolve(raw) if absolute else raw
            out.append(
                ExtractedUrl(
                    url=url,
                    raw_url=raw,
                    source=source,
                    tag=link.tag,
                    attr=link.attr,
                    text=link.text,
                )
            )
        redirect = self.meta_refresh()
        if redirect is not None:
            out.append(
                ExtractedUrl(
                    url=redirect.url,
                    raw_url=redirect.raw_url,
                    source="meta_refresh",
                    tag="meta",
                    attr="content",
                )
            )
        return out

    def forms(self) -> list[ExtractedForm]:
        out: list[ExtractedForm] = []
        for raw in self._scanner.forms:
            inputs = [
                ExtractedInput(
                    name=attrs.get("name") or "",
                    value=attrs.get("value"),
                    type=attrs.get("type"),
                )
                for attrs in raw.inputs
                if attrs.get("name") is not None
            ]
            out.append(
                ExtractedForm(
                    action=raw.attrs.get("action"),
                    method=(raw.attrs.get("method") or "get").lower(),
                    inputs=inputs,
                )
            )
        return out

    def meta_refresh(self) -> HtmlRedirect | None:
        for attrs in self._scanner.metas:
            if (attrs.get("http-equiv") or "").lower() == "refresh":
                content = attrs.get("content") or ""
                match = _REFRESH_RE.match(content)
                if match is None:
                    continue
                delay = float(match.group(1))
                raw = match.group(2).strip().strip("'\"")
                if not raw:
                    continue
                return HtmlRedirect(url=self._resolve(raw), raw_url=raw, delay=delay)
        return None

    def regex(
        self,
        pattern: str,
        *,
        group: int | str = 1,
        source: Literal["html", "visible_text"] = "html",
    ) -> str | None:
        haystack = self._html if source == "html" else self.visible_text()
        match = re.search(pattern, haystack)
        if match is None:
            return None
        return match.group(group)

    def _resolve(self, raw: str) -> str:
        if self._base_url is None:
            return raw
        return urljoin(self._base_url, raw)
