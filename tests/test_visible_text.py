from eazy_sdk.extraction.visible_text import StdlibVisibleTextExtractor


def test_strips_script_style_and_collapses_whitespace() -> None:
    html = """
    <html><head><style>.a{}</style><title>T</title></head>
    <body><script>var x=1;</script><p>Hello   world</p></body></html>
    """
    out = StdlibVisibleTextExtractor().extract(html)
    assert "Hello world" in out
    assert "var x" not in out
    assert ".a{}" not in out


def test_skips_hidden_and_display_none() -> None:
    html = (
        "<p>visible</p>"
        "<p hidden>nope</p>"
        '<div style="display:none">secret</div>'
        '<div style="visibility:hidden">gone</div>'
    )
    out = StdlibVisibleTextExtractor().extract(html)
    assert "visible" in out
    assert "nope" not in out
    assert "secret" not in out
    assert "gone" not in out


def test_hidden_subtree_hides_nested_children() -> None:
    html = '<div style="display:none"><span>a</span><b>b</b></div><p>shown</p>'
    out = StdlibVisibleTextExtractor().extract(html)
    assert "a" not in out
    assert "b" not in out
    assert "shown" in out


def test_void_elements_do_not_swallow_following_text() -> None:
    html = (
        '<html><head><meta charset="utf-8"><title>T</title></head><body><p>hello</p></body></html>'
    )
    out = StdlibVisibleTextExtractor().extract(html)
    assert "hello" in out


def test_void_element_inside_hidden_subtree_then_visible() -> None:
    html = '<div style="display:none"><img src="x.png">secret</div><p>shown</p>'
    out = StdlibVisibleTextExtractor().extract(html)
    assert "secret" not in out
    assert "shown" in out


def test_self_closing_br_does_not_break_following_text() -> None:
    html = "<p>line1<br/>line2</p><p>after</p>"
    out = StdlibVisibleTextExtractor().extract(html)
    assert "line1" in out and "line2" in out and "after" in out


def test_opacity_partial_is_visible() -> None:
    html = '<div style="opacity:0.5">half</div><div style="opacity:0">zero</div>'
    out = StdlibVisibleTextExtractor().extract(html)
    assert "half" in out
    assert "zero" not in out
