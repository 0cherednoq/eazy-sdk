"""Run Eazy SDK HTML extraction against real-world HTML fixtures.

The fixtures in ``tests/test_html`` are captured from real pages (some are
multi-megabyte). These tests prove the stdlib ``HtmlInspector`` parses real,
messy, large HTML without crashing and returns well-typed results.

These fixtures are large real-world pages kept locally and are not committed;
the suite skips when they are absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from eazy_sdk_html.forms import ExtractedForm
from eazy_sdk_html.html_inspector import HtmlInspector
from eazy_sdk_html.urls import ExtractedUrl

_FIXTURE_DIR = Path(__file__).parent / "test_html"
_FIXTURES = sorted(_FIXTURE_DIR.glob("*.html"))

if not _FIXTURES:  # pragma: no cover - depends on local, uncommitted fixtures
    pytest.skip(
        "real HTML fixtures (tests/test_html/*.html) are not present; "
        "drop real pages there locally to exercise this suite",
        allow_module_level=True,
    )


@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: p.name)
def test_inspector_handles_real_html(path: Path) -> None:
    html = path.read_bytes().decode("utf-8", errors="replace")
    inspector = HtmlInspector(html, base_url="https://example.com/")

    # None of these may raise on real, large, or malformed HTML.
    visible = inspector.visible_text()
    assert isinstance(visible, str)

    urls = inspector.urls()
    assert isinstance(urls, list)
    assert all(isinstance(u, ExtractedUrl) for u in urls)
    # A rooted relative href ("/x", not protocol-relative "//host") resolves to base.
    for u in urls:
        if u.raw_url.startswith("/") and not u.raw_url.startswith("//"):
            assert u.url.startswith("https://example.com/")

    forms = inspector.forms()
    assert isinstance(forms, list)
    assert all(isinstance(f, ExtractedForm) for f in forms)

    hidden = inspector.hidden_inputs()
    assert isinstance(hidden, dict)

    # meta lookups must not raise even when the tag is absent.
    inspector.meta_content(name="description")
    inspector.meta_content(property="og:title")
    inspector.meta_refresh()


def test_large_fixture_is_actually_large() -> None:
    # dzen.html is ~3.3 MB; confirm we really exercised a big document.
    big = max(_FIXTURES, key=lambda p: p.stat().st_size)
    assert big.stat().st_size > 1_000_000
    html = big.read_bytes().decode("utf-8", errors="replace")
    inspector = HtmlInspector(html, base_url="https://example.com/")
    # A large real page should yield a non-trivial amount of visible text and URLs.
    assert len(inspector.visible_text()) > 100
    assert len(inspector.urls()) > 0
