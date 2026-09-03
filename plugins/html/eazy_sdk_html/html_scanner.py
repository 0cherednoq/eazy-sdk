"""Single-pass HTML scanner built on the stdlib parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

_LINK_ATTRS: tuple[tuple[str, str], ...] = (
    ("a", "href"),
    ("link", "href"),
    ("script", "src"),
    ("img", "src"),
    ("iframe", "src"),
    ("form", "action"),
)

Attrs = dict[str, str | None]


@dataclass
class RawForm:
    """Raw form data collected during scanning."""

    attrs: Attrs
    inputs: list[Attrs] = field(default_factory=list)


@dataclass
class RawLink:
    """A raw link occurrence: ``(tag, attr, value, link_text)``."""

    tag: str
    attr: str
    value: str | None
    text: str | None = None


class StdlibHtmlScanner(HTMLParser):
    """Collects metas, links, forms and inputs in a single pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: list[Attrs] = []
        self.links: list[RawLink] = []
        self.inputs: list[Attrs] = []
        self.forms: list[RawForm] = []
        self._current_form: RawForm | None = None
        self._pending_link: RawLink | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d: Attrs = dict(attrs)
        if tag == "meta":
            self.metas.append(d)
        elif tag == "form":
            self._current_form = RawForm(attrs=d)
            self.forms.append(self._current_form)
        elif tag == "input":
            self.inputs.append(d)
            if self._current_form is not None:
                self._current_form.inputs.append(d)
        for link_tag, attr in _LINK_ATTRS:
            if tag == link_tag and attr in d:
                link = RawLink(tag=tag, attr=attr, value=d.get(attr))
                self.links.append(link)
                if tag == "a":
                    self._pending_link = link

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current_form = None
        if tag == "a":
            self._pending_link = None

    def handle_data(self, data: str) -> None:
        if self._pending_link is not None:
            text = data.strip()
            if text:
                prev = self._pending_link.text or ""
                self._pending_link.text = (prev + " " + text).strip()

    @classmethod
    def scan(cls, html: str) -> StdlibHtmlScanner:
        scanner = cls()
        scanner.feed(html)
        scanner.close()
        return scanner
