"""HTTP cookie-session state and explicit Set-Cookie parsing for procedural services."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie

from eazy_sdk._internal.errors import PlanError


@dataclass(frozen=True, slots=True)
class HttpCookieSession:
    """Opaque HTTP-owned session value accepted by the cookie auth binding."""

    value: str = field(repr=False)
    domain: str = ""
    path: str = ""
    secure: bool = False
    http_only: bool = False
    same_site: str = ""
    expires_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at > now


def parse_session_cookie(
    responses: Iterable[object],
    cookie_name: str,
    *,
    now: datetime,
) -> HttpCookieSession:
    """Parse the final active cookie from response envelopes/normalized responses.

    This helper is intentionally procedural: it does not add response annotations or a parsing
    DSL.  A custom cookie protocol can stay entirely inside the registration/auth service.
    """

    selected: HttpCookieSession | None = None
    for response_or_envelope in responses:
        response = getattr(response_or_envelope, "response", response_or_envelope)
        headers = getattr(response, "headers", None)
        getall = getattr(headers, "getall", None)
        if not callable(getall):
            continue
        for line in getall("set-cookie"):
            parsed = SimpleCookie()
            parsed.load(line)
            morsel = parsed.get(cookie_name)
            if morsel is None:
                continue
            expires_at: datetime | None = None
            if morsel["max-age"]:
                try:
                    max_age = int(morsel["max-age"])
                    if max_age <= 0:
                        selected = None
                        continue
                    expires_at = now + timedelta(seconds=max_age)
                except ValueError:
                    pass
            elif morsel["expires"]:
                try:
                    expires_at = parsedate_to_datetime(morsel["expires"])
                except TypeError, ValueError:
                    expires_at = None
                if expires_at is not None:
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=UTC)
                    if expires_at <= now:
                        selected = None
                        continue
            selected = (
                HttpCookieSession(
                    morsel.value,
                    domain=morsel["domain"],
                    path=morsel["path"],
                    secure=bool(morsel["secure"]),
                    http_only=bool(morsel["httponly"]),
                    same_site=morsel["samesite"],
                    expires_at=expires_at,
                )
                if morsel.value
                else None
            )
    if selected is None:
        raise PlanError(f"auth flow did not produce an active Set-Cookie {cookie_name!r}")
    return selected


__all__ = ["HttpCookieSession", "parse_session_cookie"]
