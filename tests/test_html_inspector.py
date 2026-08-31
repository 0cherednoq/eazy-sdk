import pytest

from eazy_sdk.exceptions import MissingExtractedValueError
from eazy_sdk.extraction.html_inspector import HtmlInspector

HTML = """
<html><head>
  <meta name="csrf-token" content="abc123">
  <meta http-equiv="refresh" content="5; url=/next">
</head><body>
  <form action="/login" method="post">
    <input type="hidden" name="csrf_token" value="tok">
    <input type="text" name="user" value="">
  </form>
  <a href="/page">link</a>
  <script src="https://cdn/app.js"></script>
  <p>Welcome user</p>
  <span>token=<b>XYZ</b></span>
</body></html>
"""


def inspector(base: str | None = "https://host") -> HtmlInspector:
    return HtmlInspector(HTML, base_url=base)


def test_hidden_input() -> None:
    assert inspector().hidden_input("csrf_token") == "tok"
    assert inspector().hidden_input("missing") is None
    assert inspector().hidden_input("missing", default="d") == "d"


def test_hidden_input_required_raises() -> None:
    with pytest.raises(MissingExtractedValueError):
        inspector().hidden_input("missing", required=True)


def test_meta_content() -> None:
    assert inspector().meta_content(name="csrf-token") == "abc123"
    assert inspector().meta_content(name="nope") is None


def test_meta_refresh() -> None:
    redirect = inspector().meta_refresh()
    assert redirect is not None
    assert redirect.url == "https://host/next"
    assert redirect.delay == 5.0


def test_urls_absolute() -> None:
    urls = {u.url for u in inspector().urls()}
    assert "https://host/page" in urls
    assert "https://cdn/app.js" in urls
    assert "https://host/login" in urls


def test_urls_relative_when_no_base() -> None:
    urls = {u.url for u in inspector(base=None).urls(absolute=False)}
    assert "/page" in urls


def test_forms() -> None:
    forms = inspector().forms()
    assert len(forms) == 1
    assert forms[0].action == "/login"
    assert forms[0].method == "post"
    assert forms[0].input("csrf_token") == "tok"


def test_regex_on_html_and_visible_text() -> None:
    assert inspector().regex(r"token=<b>([^<]+)</b>") == "XYZ"
    assert inspector().regex(r"(Welcome \w+)", source="visible_text") == "Welcome user"
    assert inspector().regex(r"nomatch(\d+)") is None


def test_meta_content_present_without_content_attr_required_raises() -> None:
    insp = HtmlInspector('<meta name="csrf-token">', base_url="https://host")
    assert insp.meta_content(name="csrf-token") is None
    assert insp.meta_content(name="csrf-token", default="d") == "d"
    with pytest.raises(MissingExtractedValueError):
        insp.meta_content(name="csrf-token", required=True)


def test_meta_content_requires_a_search_key() -> None:
    insp = HtmlInspector("<meta name='x' content='y'>", base_url="https://host")
    with pytest.raises(ValueError, match="name= or property="):
        insp.meta_content()


def test_meta_refresh_empty_url_is_ignored() -> None:
    insp = HtmlInspector(
        '<meta http-equiv="refresh" content="0; url=   ">', base_url="https://host"
    )
    assert insp.meta_refresh() is None
