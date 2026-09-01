"""Cloudflare Challenge Page and Turnstile protection descriptors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from eazy_sdk.ext import Malformed, NoMatch, ParsedValue, RequestScope, callable_parser
from eazy_sdk.protection import (
    PrivateBindings,
    ProtectionPersistence,
    ReplayPolicy,
    ResponseSignal,
    SignalInterception,
    SolverRequirement,
    network_identity,
    per_match,
    private_bindings,
    private_cookie_set,
    safe_method,
    until_expiry,
    until_rejected,
)

from .core import (
    BodyAccess,
    PresetBeforeCallPolicy,
    PresetChallengePolicy,
    PresetId,
    ProtectionCapabilities,
    ProtectionTemplate,
    form_field,
)

MAX_HTML_BYTES = 1_000_000


class CloudflareChallengeKind(Enum):
    MANAGED = "managed"
    INTERACTIVE = "interactive"
    JAVASCRIPT = "javascript"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CloudflareChallenge:
    kind: CloudflareChallengeKind
    page_url: str
    ray_id: str | None = None
    site_key: str | None = None


@dataclass(frozen=True, slots=True)
class SecretCookie:
    name: str
    value: str

    def __repr__(self) -> str:
        return f"SecretCookie(name={self.name!r}, value=<redacted>)"


@dataclass(frozen=True, slots=True)
class CloudflareClearance:
    cookies: tuple[SecretCookie, ...]
    user_agent: str | None = None
    expires_at: datetime | None = None

    @property
    def primary_cookie(self) -> str:
        if not self.cookies:
            raise ValueError("clearance solution has no cookies")
        return self.cookies[0].value

    def __repr__(self) -> str:
        return (
            "CloudflareClearance(cookies=<redacted>, user_agent=<redacted>, "
            f"expires_at={self.expires_at!r})"
        )


CHALLENGE_SOLVER = SolverRequirement[CloudflareChallenge, CloudflareClearance](
    "cloudflare.challenge-pages"
)
CHALLENGE_TEMPLATE = ProtectionTemplate(
    PresetId("cloudflare", "challenge-pages"),
    1,
    CHALLENGE_SOLVER,
    ProtectionCapabilities(
        BodyAccess.BUFFERED,
        cookie_jar=True,
        javascript=True,
        browser=True,
        sticky_network_identity=True,
    ),
)


def _challenge_prefilter(context: Any) -> bool:
    return bool(
        context.headers.get("cf-mitigated") == "challenge"
        and context.response.content_type == "text/html"
    )


def _parse_challenge(context: Any) -> Any:
    if not _challenge_prefilter(context):
        return NoMatch()
    if len(context.response.body) > MAX_HTML_BYTES:
        return Malformed(ValueError("Cloudflare challenge HTML exceeds the parser limit"))
    text = context.text
    if text.error is not None or not text.value:
        return Malformed(text.error or ValueError("Cloudflare challenge HTML is empty"))
    html = text.value
    ray = re.search(r"Ray ID:\s*</?[^>]*>?\s*([\w-]+)", html, re.I)
    site_key = re.search(r"data-sitekey=[\"']([^\"']+)", html, re.I)
    lowered = html.lower()
    kind = (
        CloudflareChallengeKind.INTERACTIVE
        if "interactive" in lowered
        else CloudflareChallengeKind.JAVASCRIPT
        if "jschallenge" in lowered or "javascript challenge" in lowered
        else CloudflareChallengeKind.MANAGED
        if "managed" in lowered
        else CloudflareChallengeKind.UNKNOWN
    )
    return ParsedValue(
        CloudflareChallenge(
            kind,
            context.response.url,
            ray.group(1) if ray else None,
            site_key.group(1) if site_key else None,
        )
    )


CLOUDFLARE_PARSER = callable_parser(CloudflareChallenge, _parse_challenge)


def challenge_pages(
    *,
    scope: RequestScope,
    replay: ReplayPolicy | None = None,
    persistence: ProtectionPersistence | None = None,
) -> PresetChallengePolicy:
    signal = ResponseSignal(
        "cloudflare.challenge-page",
        scope,
        CloudflareChallenge,
        CLOUDFLARE_PARSER,
        prefilter=_challenge_prefilter,
        priority=1000,
        interception=SignalInterception.DEFINITIVE,
    )
    return CHALLENGE_TEMPLATE.bind_challenge(
        scope=scope,
        signal=signal,
        apply=private_bindings(private_cookie_set(field="cookies")),
        persistence=persistence or until_rejected(scope=network_identity()),
        replay=replay or safe_method(max_replays=1),
    )


class TurnstileMode(Enum):
    MANAGED = "managed"
    NON_INTERACTIVE = "non-interactive"
    INVISIBLE = "invisible"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TurnstileChallenge:
    site_key: str
    mode: TurnstileMode
    page_url: str | None = None
    action: str | None = None


@dataclass(frozen=True, slots=True)
class TurnstileToken:
    token: str
    expires_at: datetime | None = None

    def __repr__(self) -> str:
        return f"TurnstileToken(token=<redacted>, expires_at={self.expires_at!r})"


TURNSTILE_SOLVER = SolverRequirement[TurnstileChallenge, TurnstileToken](
    "cloudflare.turnstile-widget"
)
PRECLEARANCE_SOLVER = SolverRequirement[TurnstileChallenge, CloudflareClearance](
    "cloudflare.turnstile-preclearance"
)


def _turnstile_prefilter(context: Any) -> bool:
    return b"cf-turnstile" in context.response.body[:65_536].lower()


def _parse_turnstile(context: Any) -> Any:
    if not _turnstile_prefilter(context):
        return NoMatch()
    if len(context.response.body) > MAX_HTML_BYTES:
        return Malformed(ValueError("Turnstile HTML exceeds the parser limit"))
    html = context.text.value or ""
    site_key = re.search(r"data-sitekey\s*=\s*[\"']([^\"']+)", html, re.I)
    if site_key is None:
        return Malformed(ValueError("Turnstile widget is missing data-sitekey"))
    size = re.search(r"data-size\s*=\s*[\"']([^\"']+)", html, re.I)
    appearance = re.search(r"data-appearance\s*=\s*[\"']([^\"']+)", html, re.I)
    mode_value = (
        size.group(1) if size else appearance.group(1) if appearance else "managed"
    ).lower()
    mode = {
        "managed": TurnstileMode.MANAGED,
        "normal": TurnstileMode.MANAGED,
        "compact": TurnstileMode.MANAGED,
        "non-interactive": TurnstileMode.NON_INTERACTIVE,
        "interaction-only": TurnstileMode.NON_INTERACTIVE,
        "invisible": TurnstileMode.INVISIBLE,
    }.get(mode_value, TurnstileMode.UNKNOWN)
    action = re.search(r"data-action\s*=\s*[\"']([^\"']+)", html, re.I)
    return ParsedValue(
        TurnstileChallenge(
            site_key.group(1),
            mode,
            context.response.url,
            action.group(1) if action else None,
        )
    )


TURNSTILE_PARSER = callable_parser(TurnstileChallenge, _parse_turnstile)


def turnstile_widget(
    *,
    scope: RequestScope,
    apply: PrivateBindings[TurnstileToken] | None = None,
    replay: ReplayPolicy | None = None,
    persistence: ProtectionPersistence | None = None,
) -> PresetChallengePolicy:
    signal = ResponseSignal(
        "cloudflare.turnstile-widget",
        scope,
        TurnstileChallenge,
        TURNSTILE_PARSER,
        prefilter=_turnstile_prefilter,
        interception=SignalInterception.DEFINITIVE,
    )
    template = ProtectionTemplate(
        PresetId("cloudflare", "turnstile-widget"),
        1,
        TURNSTILE_SOLVER,
        ProtectionCapabilities(BodyAccess.BUFFERED, javascript=True, browser=True),
    )
    return template.bind_challenge(
        scope=scope,
        signal=signal,
        apply=apply or form_field("cf-turnstile-response"),
        persistence=persistence or per_match(),
        replay=replay or safe_method(max_replays=1),
    )


def turnstile_preclearance(
    *,
    scope: RequestScope,
    site_key: str,
    page_url: str,
    persistence: ProtectionPersistence | None = None,
) -> PresetBeforeCallPolicy:
    if not site_key or not page_url:
        raise ValueError("Turnstile pre-clearance requires site_key and page_url")
    challenge = TurnstileChallenge(site_key, TurnstileMode.MANAGED, page_url)
    template = ProtectionTemplate(
        PresetId("cloudflare", "turnstile-preclearance"),
        1,
        PRECLEARANCE_SOLVER,
        ProtectionCapabilities(
            BodyAccess.NONE,
            cookie_jar=True,
            javascript=True,
            browser=True,
            sticky_network_identity=True,
        ),
    )
    return template.bind_before(
        scope=scope,
        apply=private_bindings(private_cookie_set(field="cookies")),
        persistence=persistence or until_expiry(scope=network_identity()),
        challenge=challenge,
    )


__all__ = [
    "CloudflareChallenge",
    "CloudflareChallengeKind",
    "CloudflareClearance",
    "SecretCookie",
    "TurnstileChallenge",
    "TurnstileMode",
    "TurnstileToken",
    "challenge_pages",
    "turnstile_preclearance",
    "turnstile_widget",
]
