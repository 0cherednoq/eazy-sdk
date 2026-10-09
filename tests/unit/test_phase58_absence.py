"""Phase 58.5: one way to send a cookie the server set, and the other ways stay gone.

A cookie the site sets travels through the identity's jar. Everything that used to carry one by
hand was removed in this phase; this test keeps it from coming back under the old names.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import eazy_sdk.auth as auth
from eazy_sdk.auth import Placed
from eazy_sdk.auth.core import AuthPlacement

REPOSITORY = Path(__file__).resolve().parents[2]
SOURCES = (
    REPOSITORY / "eazy_sdk",
    REPOSITORY / "plugins" / "browser" / "eazy_sdk_browser",
    REPOSITORY / "plugins" / "accounts" / "eazy_sdk_accounts",
    REPOSITORY / "plugins" / "sqlmodel" / "eazy_sdk_sqlmodel",
    REPOSITORY / "examples",
)
REMOVED_NAMES = (
    "session_cookie",
    "parse_session_cookie",
    "HttpCookieSession",
    "BrowserCookie",
    "BrowserCookieBridge",
    "CookieAuthAdopter",
    "browser_cookie_auth",
    "auth_cookie_set_name",
)


def test_the_cookie_session_helper_and_its_module_are_gone() -> None:
    assert not hasattr(auth, "session_cookie")
    assert "session_cookie" not in auth.__all__
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("eazy_sdk.auth.cookies")


def test_cookies_are_not_a_session_placement() -> None:
    assert not hasattr(Placed, "cookie")
    assert not hasattr(Placed, "cookies")
    assert "many" not in AuthPlacement.__dataclass_fields__
    assert not hasattr(AuthPlacement, "pairs")


def test_the_browser_plugin_has_no_cookie_record_or_single_cookie_bridge_of_its_own() -> None:
    browser = pytest.importorskip("eazy_sdk_browser")
    for name in (
        "BrowserCookie",
        "BrowserCookieBridge",
        "CookieAuthAdopter",
        "browser_cookie_auth",
    ):
        assert not hasattr(browser, name), name
        assert name not in browser.__all__, name


def test_the_removed_names_appear_nowhere_in_the_sources() -> None:
    found: list[str] = []
    for root in SOURCES:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            found.extend(
                f"{path.relative_to(REPOSITORY)}: {name}" for name in REMOVED_NAMES if name in text
            )
    assert not found, "\n".join(found)


def test_what_stays_is_the_declaration_and_the_callers_own_cookie() -> None:
    """The two ways that remain answer two different questions, so they are not alternatives."""

    from eazy_sdk import Cookie
    from eazy_sdk.auth import Cookies, CookieScheme
    from eazy_sdk.response import FromCookie

    assert Cookies().required == ()
    assert CookieScheme("api_key").placements[0].name == "api_key"
    assert FromCookie("act").name == "act"
    assert Cookie is not None
