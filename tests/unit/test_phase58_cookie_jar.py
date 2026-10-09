"""Phase 58.1: a cookie jar that stores and selects the way a browser does.

The scenario is a webmail login spread over three hosts: the account host sets a device cookie,
the auth host sets the session for the whole site, and the mail host must receive exactly the
cookies meant for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from eazy_sdk.cookies import (
    MAX_COOKIES,
    MAX_COOKIES_PER_DOMAIN,
    CookieJar,
    CookieState,
    StoredCookie,
)

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


class Clock:
    """A clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def _jar(**options: object) -> tuple[CookieJar, Clock]:
    clock = Clock()
    return CookieJar(clock=clock, **options), clock  # type: ignore[arg-type]


def _names(jar: CookieJar, url: str) -> list[str]:
    return [name for name, _ in jar.header_pairs(url)]


# --- parsing ----------------------------------------------------------------------------------


def test_a_name_and_a_value_are_stored_as_written() -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/", ["sid=abc123", 'quoted="a b"', "empty=", "eq=a=b"])
    assert jar.header_pairs("https://mail.example/") == (
        ("sid", "abc123"),
        ("quoted", '"a b"'),
        ("empty", ""),
        ("eq", "a=b"),
    )


@pytest.mark.parametrize("line", ["", "novalue", "=orphan", "  ; Path=/"])
def test_a_line_without_a_name_or_an_equals_sign_is_ignored(line: str) -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/", [line])
    assert len(jar) == 0


def test_attribute_names_ignore_case_and_unknown_ones_are_skipped() -> None:
    jar, _ = _jar()
    jar.store(
        "https://mail.example/",
        ["sid=1; PATH=/api; SECURE; httponly; SameSite=lax; Priority=High; Partitioned"],
    )
    (cookie,) = jar.snapshot()
    assert (cookie.path, cookie.secure, cookie.http_only, cookie.same_site) == (
        "/api",
        True,
        True,
        "Lax",
    )


def test_an_address_without_a_host_stores_and_selects_nothing() -> None:
    jar, _ = _jar()
    jar.store("not a url", ["sid=1"])
    jar.store("http://[broken/", ["sid=1"])
    assert len(jar) == 0
    assert jar.select("/relative") == ()


# --- which host a cookie belongs to -----------------------------------------------------------


def test_a_cookie_without_a_domain_belongs_to_the_host_that_set_it() -> None:
    jar, _ = _jar()
    jar.store("https://auth.mail.example/login", ["act=csrf"])
    (cookie,) = jar.snapshot()
    assert (cookie.domain, cookie.host_only) == ("auth.mail.example", True)
    assert _names(jar, "https://auth.mail.example/") == ["act"]
    assert _names(jar, "https://sub.auth.mail.example/") == []
    assert _names(jar, "https://mail.example/") == []


@pytest.mark.parametrize(
    "attribute", ["mail.example", ".mail.example", "MAIL.Example", "mail.example."]
)
def test_a_domain_cookie_reaches_the_domain_and_everything_under_it(attribute: str) -> None:
    jar, _ = _jar()
    jar.store("https://auth.mail.example/login", [f"sid=1; Domain={attribute}"])
    (cookie,) = jar.snapshot()
    assert (cookie.domain, cookie.host_only) == ("mail.example", False)
    assert _names(jar, "https://mail.example/") == ["sid"]
    assert _names(jar, "https://e.mail.example/") == ["sid"]
    assert _names(jar, "https://deep.e.mail.example/") == ["sid"]


def test_a_domain_is_matched_on_a_label_boundary() -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/", ["sid=1; Domain=mail.example"])
    assert _names(jar, "https://notmail.example/") == []
    assert _names(jar, "https://mail.example.evil.test/") == []


@pytest.mark.parametrize(
    "attribute",
    ["other.example", "e.mail.example", "example", "com"],
    ids=["foreign", "sibling-below", "parent-single-label", "public-suffix"],
)
def test_a_server_cannot_set_a_cookie_for_a_domain_it_is_not_under(attribute: str) -> None:
    jar, _ = _jar()
    jar.store("https://auth.mail.example/", [f"sid=1; Domain={attribute}"])
    assert len(jar) == 0


def test_a_domain_equal_to_the_host_is_a_domain_cookie_even_on_one_label() -> None:
    jar, _ = _jar()
    jar.store("http://localhost:8080/", ["sid=1; Domain=localhost"])
    (cookie,) = jar.snapshot()
    assert (cookie.domain, cookie.host_only) == ("localhost", False)


def test_a_public_suffix_list_refuses_what_the_default_rule_cannot_see() -> None:
    suffixes = {"co.uk", "uk"}
    jar, _ = _jar(public_suffixes=lambda domain: domain in suffixes)
    jar.store("https://shop.co.uk/", ["wide=1; Domain=co.uk", "own=1; Domain=shop.co.uk"])
    assert [cookie.name for cookie in jar.snapshot()] == ["own"]

    lenient, _ = _jar()
    lenient.store("https://shop.co.uk/", ["wide=1; Domain=co.uk"])
    assert len(lenient) == 1


def test_an_ip_address_takes_only_host_cookies() -> None:
    jar, _ = _jar()
    jar.store("http://10.0.0.5/", ["sid=1", "wide=1; Domain=0.0.5"])
    assert [cookie.name for cookie in jar.snapshot()] == ["sid"]
    assert _names(jar, "http://10.0.0.5/") == ["sid"]


def test_the_port_and_the_case_of_the_host_do_not_matter() -> None:
    jar, _ = _jar()
    jar.store("https://Mail.Example:8443/", ["sid=1"])
    assert _names(jar, "https://mail.example/") == ["sid"]
    assert _names(jar, "https://mail.example:9000/") == ["sid"]


# --- paths ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("request_path", "stored"),
    [
        ("/cgi-bin/auth", "/cgi-bin"),
        ("/cgi-bin/", "/cgi-bin"),
        ("/login", "/"),
        ("/", "/"),
        ("", "/"),
        ("/a/b/c", "/a/b"),
    ],
)
def test_the_default_path_is_the_directory_of_the_request(request_path: str, stored: str) -> None:
    jar, _ = _jar()
    jar.store(f"https://mail.example{request_path}", ["sid=1"])
    assert jar.snapshot().cookies[0].path == stored


def test_a_path_that_does_not_start_with_a_slash_falls_back_to_the_default() -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/a/b", ["sid=1; Path=relative"])
    assert jar.snapshot().cookies[0].path == "/a"


@pytest.mark.parametrize(
    ("cookie_path", "request_path", "sent"),
    [
        ("/api", "/api", True),
        ("/api", "/api/folders", True),
        ("/api", "/apiary", False),
        ("/api/", "/api/folders", True),
        ("/api/", "/api", False),
        ("/", "/anything/at/all", True),
        ("/api", "/", False),
    ],
)
def test_a_path_matches_on_a_segment_boundary(
    cookie_path: str, request_path: str, sent: bool
) -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/", [f"sid=1; Path={cookie_path}"])
    assert bool(_names(jar, f"https://mail.example{request_path}")) is sent


def test_a_longer_path_comes_first_and_equal_paths_keep_the_order_they_were_set() -> None:
    jar, _ = _jar()
    jar.store(
        "https://mail.example/",
        ["first=1; Path=/", "deep=1; Path=/api/v1", "second=1; Path=/", "mid=1; Path=/api"],
    )
    assert _names(jar, "https://mail.example/api/v1/x") == ["deep", "mid", "first", "second"]


# --- expiry -----------------------------------------------------------------------------------


def test_a_cookie_without_an_expiry_is_kept() -> None:
    jar, clock = _jar()
    jar.store("https://mail.example/", ["sid=1"])
    clock.advance(days=3650)
    assert _names(jar, "https://mail.example/") == ["sid"]
    assert jar.snapshot().cookies[0].is_session()


def test_max_age_wins_over_expires() -> None:
    jar, clock = _jar()
    jar.store(
        "https://mail.example/",
        ["sid=1; Max-Age=60; Expires=Wed, 09 Oct 2030 12:00:00 GMT"],
    )
    assert jar.snapshot().cookies[0].expires_at == NOW + timedelta(seconds=60)
    clock.advance(seconds=61)
    assert _names(jar, "https://mail.example/") == []


def test_expires_is_read_when_there_is_no_max_age() -> None:
    jar, clock = _jar()
    jar.store("https://mail.example/", ["sid=1; Expires=Fri, 09 Oct 2026 13:00:00 GMT"])
    assert jar.snapshot().cookies[0].expires_at == NOW + timedelta(hours=1)
    clock.advance(hours=2)
    assert len(jar.snapshot()) == 0


@pytest.mark.parametrize(
    "removal",
    ["sid=; Max-Age=0", "sid=; Max-Age=-1", "sid=gone; Expires=Thu, 01 Jan 1970 00:00:00 GMT"],
)
def test_a_server_removes_a_cookie_by_expiring_it(removal: str) -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/", ["sid=1", "other=1"])
    jar.store("https://mail.example/", [removal])
    assert _names(jar, "https://mail.example/") == ["other"]


def test_an_expiry_that_does_not_parse_leaves_a_cookie_without_one() -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/", ["a=1; Max-Age=soon", "b=1; Expires=whenever"])
    assert [cookie.expires_at for cookie in jar.snapshot()] == [None, None]


def test_a_live_cookie_is_asked_for_by_name_with_a_safety_margin() -> None:
    jar, clock = _jar()
    jar.store("https://mail.example/", ["sid=1; Max-Age=100", "forever=1"])
    assert jar.has_live("sid")
    assert jar.has_live("sid", leeway=timedelta(seconds=30))
    assert not jar.has_live("sid", leeway=timedelta(seconds=200))
    assert jar.has_live("forever", leeway=timedelta(days=365))
    assert not jar.has_live("missing")
    clock.advance(seconds=101)
    assert not jar.has_live("sid")


# --- Secure -----------------------------------------------------------------------------------


def test_a_secure_cookie_goes_only_over_https_and_to_a_loopback_stand() -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/", ["sid=1; Secure"])
    assert _names(jar, "https://mail.example/") == ["sid"]
    assert _names(jar, "http://mail.example/") == []

    local, _ = _jar()
    local.store("http://localhost:8000/", ["sid=1; Secure"])
    local.store("http://127.0.0.1:8000/", ["sid=1; Secure"])
    assert _names(local, "http://localhost:8000/") == ["sid"]
    assert _names(local, "http://127.0.0.1:8000/") == ["sid"]


# --- replacing and coexisting -----------------------------------------------------------------


def test_the_same_name_domain_and_path_replace_and_keep_their_place() -> None:
    jar, _ = _jar()
    jar.store("https://mail.example/", ["a=1", "b=1"])
    jar.store("https://mail.example/", ["a=2"])
    assert jar.header_pairs("https://mail.example/") == (("a", "2"), ("b", "1"))


def test_one_name_lives_twice_on_two_domains_or_two_paths() -> None:
    jar, _ = _jar()
    jar.store("https://auth.mail.example/", ["sid=auth"])
    jar.store("https://e.mail.example/", ["sid=mail", "sid=api; Path=/api"])
    assert jar.header_pairs("https://auth.mail.example/") == (("sid", "auth"),)
    assert jar.header_pairs("https://e.mail.example/api/x") == (("sid", "api"), ("sid", "mail"))
    assert len(jar) == 3


def test_three_hosts_each_receive_only_their_own_cookies() -> None:
    """The login of the motivating site: a device cookie, a site-wide session, a host token."""

    jar, _ = _jar()
    jar.store(
        "https://account.mail.example/login",
        ["act=csrf; Path=/", "device=d1; Domain=mail.example; Path=/"],
    )
    jar.store(
        "https://auth.mail.example/cgi-bin/auth",
        ["s=session; Domain=mail.example; Path=/", "ssdc=sync; Path=/"],
    )
    jar.store("https://e.mail.example/sdc", ["sdcs=mailbox; Path=/"])
    assert sorted(_names(jar, "https://account.mail.example/")) == ["act", "device", "s"]
    assert sorted(_names(jar, "https://auth.mail.example/sdc")) == ["device", "s", "ssdc"]
    assert sorted(_names(jar, "https://e.mail.example/inbox")) == ["device", "s", "sdcs"]
    assert _names(jar, "https://other.example/") == []


# --- limits -----------------------------------------------------------------------------------


def test_an_overfull_domain_drops_the_cookies_used_longest_ago() -> None:
    jar, _ = _jar()
    lines = [f"c{index}=1" for index in range(MAX_COOKIES_PER_DOMAIN)]
    jar.store("https://mail.example/", lines)
    jar.select("https://mail.example/")  # everything is used once, in order
    jar.store("https://mail.example/", ["fresh=1"])
    names = {cookie.name for cookie in jar.snapshot()}
    assert len(names) == MAX_COOKIES_PER_DOMAIN
    assert "fresh" in names and "c0" not in names and "c1" in names


def test_the_whole_jar_has_a_ceiling() -> None:
    jar, _ = _jar()
    for index in range(MAX_COOKIES // MAX_COOKIES_PER_DOMAIN + 2):
        jar.store(
            f"https://host{index}.example/",
            [f"c{item}=1" for item in range(MAX_COOKIES_PER_DOMAIN)],
        )
    assert len(jar) == MAX_COOKIES


# --- snapshots --------------------------------------------------------------------------------


def test_a_snapshot_round_trips_through_its_primitive_form_without_loss() -> None:
    jar, _ = _jar()
    jar.store(
        "https://auth.mail.example/cgi-bin/auth",
        [
            "session=keep-me",
            "s=1; Domain=mail.example; Path=/; Max-Age=3600; Secure; HttpOnly; SameSite=None",
        ],
    )
    state = jar.snapshot()
    restored = CookieState.from_primitive(state.to_primitive())
    assert restored == state
    assert [cookie.is_session() for cookie in restored] == [True, False]

    again, _ = _jar()
    again.load(restored)
    assert again.header_pairs("https://auth.mail.example/cgi-bin/x") == jar.header_pairs(
        "https://auth.mail.example/cgi-bin/x"
    )


def test_a_jar_starts_from_a_snapshot_and_merges_another_by_key() -> None:
    first = CookieState(
        (StoredCookie("sid", "old", "mail.example"), StoredCookie("a", "1", "mail.example"))
    )
    jar = CookieJar(first, clock=Clock())
    jar.load(CookieState((StoredCookie("sid", "new", "mail.example"),)))
    assert jar.header_pairs("https://mail.example/") == (("sid", "new"), ("a", "1"))


def test_an_expired_cookie_is_neither_loaded_nor_written_out() -> None:
    expired = StoredCookie("old", "1", "mail.example", expires_at=NOW - timedelta(seconds=1))
    alive = StoredCookie("new", "1", "mail.example", expires_at=NOW + timedelta(seconds=10))
    jar = CookieJar(CookieState((expired, alive)), clock=(clock := Clock()))
    assert [cookie.name for cookie in jar.snapshot()] == ["new"]
    clock.advance(seconds=11)
    assert jar.snapshot().is_empty()


def test_the_value_stays_out_of_a_repr() -> None:
    assert "secret-value" not in repr(StoredCookie("sid", "secret-value", "mail.example"))


# --- a record that cannot be right is refused -------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"name": "", "value": "1", "domain": "mail.example"}, "needs a name"),
        ({"name": "sid", "value": "1", "domain": ""}, "needs the host"),
        ({"name": "sid", "value": "1", "domain": ".mail.example"}, "without the leading dot"),
        ({"name": "sid", "value": "1", "domain": "Mail.Example"}, "must be lowercase"),
        ({"name": "sid", "value": "1", "domain": "mail.example", "path": "api"}, "must start with"),
        (
            {
                "name": "sid",
                "value": "1",
                "domain": "mail.example",
                "expires_at": datetime(2030, 1, 1),
            },
            "timezone-aware",
        ),
    ],
)
def test_a_stored_cookie_that_could_not_be_sent_anywhere_is_refused(
    arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        StoredCookie(**arguments)  # type: ignore[arg-type]


def test_a_stored_record_without_its_scope_is_not_guessed_at() -> None:
    """A record written by something that encoded the scope in a leading dot is not this format."""

    old = {"name": "sid", "value": "1", "domain": ".mail.example", "path": "/", "expires_at": None}
    with pytest.raises(ValueError, match="lacks host_only"):
        CookieState.from_primitive([old])
    with pytest.raises(ValueError, match="list of cookies"):
        CookieState.from_primitive({"sid": "1"})
    with pytest.raises(ValueError, match="host_only is a boolean"):
        CookieState.from_primitive([{**old, "domain": "mail.example", "host_only": "yes"}])


# --- what is stored for a host is what that host gets back -------------------------------------

_LABEL = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8)
_NAME = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=10)
_VALUE = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-._", max_size=16)


@given(
    host=st.lists(_LABEL, min_size=2, max_size=4).map(".".join),
    cookies=st.dictionaries(_NAME, _VALUE, min_size=1, max_size=8),
    other=st.lists(_LABEL, min_size=2, max_size=4).map(".".join),
)
def test_a_host_gets_back_exactly_what_it_stored(
    host: str, cookies: dict[str, str], other: str
) -> None:
    jar = CookieJar(clock=Clock())
    jar.store(f"https://{host}/", [f"{name}={value}" for name, value in cookies.items()])
    assert dict(jar.header_pairs(f"https://{host}/any/path")) == cookies
    if other != host:
        assert jar.header_pairs(f"https://{other}/") == ()


def test_concurrent_writers_lose_nothing() -> None:
    import threading

    jar = CookieJar(clock=Clock())

    def write(worker: int) -> None:
        for item in range(MAX_COOKIES_PER_DOMAIN):
            jar.store(f"https://w{worker}.mail.example/", [f"c{item}={worker}"])
            jar.select(f"https://w{worker}.mail.example/")

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(jar) == 8 * MAX_COOKIES_PER_DOMAIN
