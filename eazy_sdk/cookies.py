"""Cookies a server sets, stored and sent back the way a browser does it.

A cookie the server sets belongs to nobody's declaration: the site names it, scopes it to a host
and a path, and expects it back there until it expires. This module is that bookkeeping, and
nothing else. It sends nothing and knows no HTTP client::

    jar = CookieJar()
    jar.store("https://auth.mail.example/login", ["sid=abc; Domain=mail.example; Path=/"])
    jar.header_pairs("https://api.mail.example/folders")     # (("sid", "abc"),)
    jar.header_pairs("https://other.example/")               # ()

Storage and selection follow RFC 6265, sections 5.3 and 5.4. Three things are spelled out here
because other jars get them wrong in ways that only show up as a refused login:

* A cookie without ``Domain`` belongs to the host that set it and to no other. There is no
  unbound cookie that travels everywhere.
* A cookie without an expiry is kept and saved like any other. For a login those are usually
  the ones that matter.
* ``Domain`` is written without a leading dot, and whether subdomains receive the cookie is the
  separate ``host_only`` flag. A dot would mean something only by convention.
"""

from __future__ import annotations

import ipaddress
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

__all__ = ["CookieJar", "CookieState", "Cookies", "StoredCookie"]

MAX_COOKIES_PER_DOMAIN = 50
MAX_COOKIES = 3000
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_MAX_AGE = re.compile(r"-?\d+")
_SAME_SITE = {"strict": "Strict", "lax": "Lax", "none": "None"}

type PublicSuffixes = Callable[[str], bool]
"""Answers whether a domain is a public suffix, under which no site may set a cookie."""


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Cookies:
    """Declare that a site keeps its session in cookies, so the SDK carries them like a browser.

    Written once, on the SDK root or on a router, beside ``security``::

        class MailSdk(AsyncRoot):
            cookies = Cookies(required=("sid",))

    From then on every cookie the site sets is sent back where it belongs: to the next
    operation, to the next hop of a redirect, to another host of the same site. Nobody names
    them. ``required`` is the one exception: the cookies without which a session is no session.
    """

    required: tuple[str, ...] = ()
    """Cookies that must be alive for a session to be valid."""
    leeway: timedelta = timedelta(seconds=30)
    """A required cookie that expires sooner than this no longer counts as alive."""
    public_suffixes: PublicSuffixes | None = None
    """Answers whether a domain is a public suffix; without it only one-label domains are."""

    def __post_init__(self) -> None:
        if isinstance(self.required, str) or not isinstance(self.required, tuple):
            raise TypeError("Cookies(required=...) is a tuple of cookie names")
        if any(not isinstance(name, str) or not name for name in self.required):
            raise ValueError("Cookies(required=...) names a cookie with an empty string")
        if len(set(self.required)) != len(self.required):
            raise ValueError("Cookies(required=...) names a cookie twice")
        if self.leeway < timedelta(0):
            raise ValueError("Cookies(leeway=...) cannot be negative")


@dataclass(frozen=True, slots=True)
class StoredCookie:
    """One cookie with everything that decides where it goes and for how long."""

    name: str
    value: str = field(repr=False)
    domain: str
    """The host that set it, or its ``Domain`` attribute: lowercase, no leading dot."""
    path: str = "/"
    host_only: bool = True
    """``True`` sends it to ``domain`` alone; ``False`` to its subdomains as well."""
    expires_at: datetime | None = None
    """``None`` is a cookie without an expiry; it is kept until the server removes it."""
    secure: bool = False
    http_only: bool = False
    same_site: str = ""
    """Kept as received; it does not decide where the cookie is sent."""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a stored cookie needs a name")
        if not self.domain:
            raise ValueError(f"cookie {self.name!r} needs the host it belongs to")
        if self.domain.startswith("."):
            raise ValueError(
                f"cookie {self.name!r}: domain {self.domain!r} is written without the leading "
                "dot; host_only=False is what sends a cookie to subdomains"
            )
        if self.domain != self.domain.lower():
            raise ValueError(f"cookie {self.name!r}: domain {self.domain!r} must be lowercase")
        if not self.path.startswith("/"):
            raise ValueError(f"cookie {self.name!r}: path {self.path!r} must start with '/'")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError(f"cookie {self.name!r}: expires_at must be timezone-aware")

    @property
    def key(self) -> tuple[str, str, str]:
        """What makes two cookies the same cookie: a later one replaces an earlier one."""

        return (self.name, self.domain, self.path)

    def is_session(self) -> bool:
        """A cookie without an expiry: a browser keeps it until it is closed."""

        return self.expires_at is None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def to_primitive(self) -> dict[str, object]:
        """A JSON-ready mapping; the one form in which a cookie is written to storage."""

        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "host_only": self.host_only,
            "expires_at": None if self.expires_at is None else self.expires_at.isoformat(),
            "secure": self.secure,
            "http_only": self.http_only,
            "same_site": self.same_site,
        }

    @classmethod
    def from_primitive(cls, data: Mapping[str, object]) -> StoredCookie:
        """Read what ``to_primitive`` wrote; every field must be there.

        A record without ``host_only`` is not guessed at: it was written by something that
        encoded the scope another way, and a wrong guess sends the cookie to the wrong hosts.
        """

        missing = sorted(
            name
            for name in ("name", "value", "domain", "path", "host_only", "expires_at")
            if name not in data
        )
        if missing:
            raise ValueError(f"stored cookie lacks {', '.join(missing)}")
        name, value, domain, path = (data[key] for key in ("name", "value", "domain", "path"))
        host_only, expires = data["host_only"], data["expires_at"]
        if not all(isinstance(item, str) for item in (name, value, domain, path)):
            raise ValueError("stored cookie name, value, domain and path are strings")
        if not isinstance(host_only, bool):
            raise ValueError("stored cookie host_only is a boolean")
        if expires is not None and not isinstance(expires, str):
            raise ValueError("stored cookie expires_at is an ISO timestamp or null")
        return cls(
            name=str(name),
            value=str(value),
            domain=str(domain),
            path=str(path),
            host_only=host_only,
            expires_at=None if expires is None else datetime.fromisoformat(expires),
            secure=bool(data.get("secure", False)),
            http_only=bool(data.get("http_only", False)),
            same_site=str(data.get("same_site", "") or ""),
        )


@dataclass(frozen=True, slots=True)
class CookieState:
    """An immutable snapshot of a jar: the form cookies take outside of one.

    A session store keeps it beside the session, a browser hands it over after a login, and an
    identity starts from it.
    """

    cookies: tuple[StoredCookie, ...] = ()

    def __iter__(self) -> Iterator[StoredCookie]:
        return iter(self.cookies)

    def __len__(self) -> int:
        return len(self.cookies)

    def is_empty(self) -> bool:
        return not self.cookies

    def to_primitive(self) -> list[dict[str, object]]:
        return [cookie.to_primitive() for cookie in self.cookies]

    @classmethod
    def from_primitive(cls, data: object) -> CookieState:
        if not isinstance(data, list | tuple):
            raise ValueError("a stored cookie state is a list of cookies")
        cookies: list[StoredCookie] = []
        for item in data:
            if not isinstance(item, Mapping):
                raise ValueError("a stored cookie is a mapping")
            cookies.append(StoredCookie.from_primitive(item))
        return cls(tuple(cookies))


@dataclass(frozen=True, slots=True)
class _Target:
    """The parts of an address a jar compares cookies against."""

    scheme: str
    host: str
    path: str

    @property
    def is_secure(self) -> bool:
        return self.scheme == "https" or self.host in _LOOPBACK_HOSTS


def _target(url: str) -> _Target | None:
    try:
        split = urlsplit(url)
        host = (split.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host:
        return None
    return _Target(split.scheme.lower(), host, split.path or "/")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _domain_matches(host: str, domain: str) -> bool:
    """RFC 6265, 5.1.3: the host is the domain, or a name under it."""

    if host == domain:
        return True
    return host.endswith("." + domain) and not _is_ip(host)


def _default_path(request_path: str) -> str:
    """RFC 6265, 5.1.4: the directory of the request path."""

    if not request_path.startswith("/") or request_path.count("/") == 1:
        return "/"
    return request_path[: request_path.rindex("/")]


def _path_matches(request_path: str, cookie_path: str) -> bool:
    """RFC 6265, 5.1.4: the cookie path is a prefix that ends on a segment boundary."""

    if request_path == cookie_path:
        return True
    if not request_path.startswith(cookie_path):
        return False
    return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"


def _expiry(attributes: Mapping[str, str], now: datetime) -> tuple[datetime | None, bool]:
    """The expiry a cookie states, and whether stating it removes the cookie.

    ``Max-Age`` wins over ``Expires`` (RFC 6265, 5.3 step 3). An attribute that does not parse
    is ignored, which leaves a cookie without an expiry rather than without a cookie.
    """

    max_age = attributes.get("max-age")
    if max_age is not None and _MAX_AGE.fullmatch(max_age):
        seconds = int(max_age)
        if seconds <= 0:
            return now, True
        return now + timedelta(seconds=seconds), False
    expires = attributes.get("expires")
    if expires:
        try:
            moment = parsedate_to_datetime(expires)
        except (TypeError, ValueError):
            return None, False
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment, moment <= now
    return None, False


def _parse(line: str) -> tuple[str, str, dict[str, str]] | None:
    """One ``Set-Cookie`` field line as a name, a value and its attributes by lowercase name."""

    pair, _, rest = line.partition(";")
    name, separator, value = pair.partition("=")
    name, value = name.strip(), value.strip()
    if not separator or not name:
        return None
    attributes: dict[str, str] = {}
    for chunk in rest.split(";"):
        key, _, item = chunk.partition("=")
        key = key.strip().lower()
        if key:
            # The last occurrence of an attribute wins, as it does in a browser.
            attributes[key] = item.strip()
    return name, value, attributes


def _scope(
    host: str, attribute: str, public_suffixes: PublicSuffixes | None
) -> tuple[str, bool] | None:
    """Where a cookie belongs, or ``None`` when the server may not set it there."""

    domain = attribute.strip().lstrip(".").lower().rstrip(".")
    if not domain or domain == host:
        return host, not domain
    # Without a suffix list the one certain case is refused: a single label is never a site's
    # own domain, so ``Domain=com`` cannot be honoured.
    is_suffix = public_suffixes(domain) if public_suffixes is not None else "." not in domain
    if is_suffix or not _domain_matches(host, domain):
        return None
    return domain, False


class CookieJar:
    """The cookies of one user: stored as a server sets them, selected for each address.

    Safe to share between threads and tasks. Every operation is short and holds no lock across
    I/O, so concurrent calls of one identity neither lose a cookie nor wait on each other.
    """

    def __init__(
        self,
        state: CookieState | None = None,
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # Insertion order is creation order: replacing a cookie keeps its place (RFC 6265,
        # 5.3 step 11.3), and that order is what breaks ties when the header is built.
        self._cookies: dict[tuple[str, str, str], StoredCookie] = {}
        self._used: dict[tuple[str, str, str], int] = {}
        self._tick = 0
        if state is not None:
            self.load(state)

    def __len__(self) -> int:
        with self._lock:
            return len(self._cookies)

    def store(
        self,
        url: str,
        set_cookie_lines: Iterable[str],
        *,
        public_suffixes: PublicSuffixes | None = None,
    ) -> None:
        """Take the ``Set-Cookie`` lines of a response that came from ``url``.

        The suffix list is what the site's declaration knows, and the jar belongs to a user who
        may talk to several sites, so it arrives with the response rather than with the jar.
        """

        target = _target(url)
        if target is None:
            return
        now = self._clock()
        with self._lock:
            for line in set_cookie_lines:
                self._store_line(target, line, now, public_suffixes)
            self._evict(now)

    def select(self, url: str) -> tuple[StoredCookie, ...]:
        """The cookies a request to ``url`` carries, in the order they are written."""

        target = _target(url)
        if target is None:
            return ()
        now = self._clock()
        with self._lock:
            self._drop_expired(now)
            chosen = [cookie for cookie in self._cookies.values() if self._belongs(cookie, target)]
            for cookie in chosen:
                self._touch(cookie.key)
        # A longer path first; among equal paths the earlier cookie first (RFC 6265, 5.4).
        # ``sorted`` is stable, so creation order survives as the tie-breaker.
        return tuple(sorted(chosen, key=lambda cookie: -len(cookie.path)))

    def header_pairs(self, url: str) -> tuple[tuple[str, str], ...]:
        """``select`` as the name and value pairs of a ``Cookie`` header."""

        return tuple((cookie.name, cookie.value) for cookie in self.select(url))

    def has_live(self, name: str, *, leeway: timedelta = timedelta(0)) -> bool:
        """Whether some cookie called ``name`` outlives ``leeway`` from now."""

        horizon = self._clock() + leeway
        with self._lock:
            return any(
                cookie.name == name and not cookie.is_expired(horizon)
                for cookie in self._cookies.values()
            )

    def load(self, state: CookieState) -> None:
        """Merge a snapshot in; a cookie already here under the same key is replaced."""

        now = self._clock()
        with self._lock:
            for cookie in state:
                if cookie.is_expired(now):
                    continue
                self._cookies[cookie.key] = cookie
                self._touch(cookie.key)
            self._evict(now)

    def snapshot(self) -> CookieState:
        """Everything still alive, cookies without an expiry included."""

        now = self._clock()
        with self._lock:
            self._drop_expired(now)
            return CookieState(tuple(self._cookies.values()))

    # --- under the lock ---------------------------------------------------------------------

    def _store_line(
        self, target: _Target, line: str, now: datetime, public_suffixes: PublicSuffixes | None
    ) -> None:
        parsed = _parse(line)
        if parsed is None:
            return
        name, value, attributes = parsed
        scope = _scope(target.host, attributes.get("domain", ""), public_suffixes)
        if scope is None:
            return
        domain, host_only = scope
        path = attributes.get("path", "")
        if not path.startswith("/"):
            path = _default_path(target.path)
        expires_at, removes = _expiry(attributes, now)
        key = (name, domain, path)
        if removes:
            self._cookies.pop(key, None)
            self._used.pop(key, None)
            return
        self._cookies[key] = StoredCookie(
            name=name,
            value=value,
            domain=domain,
            path=path,
            host_only=host_only,
            expires_at=expires_at,
            secure="secure" in attributes,
            http_only="httponly" in attributes,
            same_site=_SAME_SITE.get(attributes.get("samesite", "").lower(), ""),
        )
        self._touch(key)

    def _belongs(self, cookie: StoredCookie, target: _Target) -> bool:
        if cookie.host_only:
            if target.host != cookie.domain:
                return False
        elif not _domain_matches(target.host, cookie.domain):
            return False
        if cookie.secure and not target.is_secure:
            return False
        return _path_matches(target.path, cookie.path)

    def _touch(self, key: tuple[str, str, str]) -> None:
        self._tick += 1
        self._used[key] = self._tick

    def _drop_expired(self, now: datetime) -> None:
        for key in [key for key, cookie in self._cookies.items() if cookie.is_expired(now)]:
            del self._cookies[key]
            self._used.pop(key, None)

    def _evict(self, now: datetime) -> None:
        """Expired cookies go first, then the least recently used of an overfull domain."""

        self._drop_expired(now)
        by_domain: dict[str, list[tuple[str, str, str]]] = {}
        for key, cookie in self._cookies.items():
            by_domain.setdefault(cookie.domain, []).append(key)
        for keys in by_domain.values():
            self._drop_least_used(keys, len(keys) - MAX_COOKIES_PER_DOMAIN)
        self._drop_least_used(list(self._cookies), len(self._cookies) - MAX_COOKIES)

    def _drop_least_used(self, keys: list[tuple[str, str, str]], excess: int) -> None:
        if excess <= 0:
            return
        for key in sorted(keys, key=lambda item: self._used.get(item, 0))[:excess]:
            self._cookies.pop(key, None)
            self._used.pop(key, None)
