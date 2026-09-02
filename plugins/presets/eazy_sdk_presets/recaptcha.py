"""Google reCAPTCHA v2, v3 and Enterprise lifecycle descriptors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from eazy_sdk.ext import Malformed, NoMatch, ParsedValue, RequestScope
from eazy_sdk.protection import (
    ChallengeSolver,
    PrivateBindings,
    ProtectionPersistence,
    ReplayPolicy,
    ResponseSignal,
    SignalInterception,
    SolverRequirement,
    per_call,
    per_match,
    safe_method,
)
from eazy_sdk.response import callable_parser

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


class RecaptchaMode(Enum):
    V2_CHECKBOX = "v2-checkbox"
    V2_INVISIBLE = "v2-invisible"
    V3_ACTION = "v3-action"
    ENTERPRISE_CHECKBOX = "enterprise-checkbox"
    ENTERPRISE_SCORE = "enterprise-score-action"
    ENTERPRISE_POLICY = "enterprise-policy-based-challenge"


@dataclass(frozen=True, slots=True)
class RecaptchaChallenge:
    site_key: str
    mode: RecaptchaMode
    page_url: str | None = None
    action: str | None = None
    project: str | None = None
    theme: str | None = None
    size: str | None = None
    tabindex: str | None = None
    callback: str | None = None
    badge: str | None = None


@dataclass(frozen=True, slots=True)
class RecaptchaToken:
    token: str
    expires_at: datetime | None = None

    def __repr__(self) -> str:
        return f"RecaptchaToken(token=<redacted>, expires_at={self.expires_at!r})"


V2_CHECKBOX_SOLVER = SolverRequirement[RecaptchaChallenge, RecaptchaToken](
    "google.recaptcha-v2-checkbox"
)
V2_INVISIBLE_SOLVER = SolverRequirement[RecaptchaChallenge, RecaptchaToken](
    "google.recaptcha-v2-invisible"
)
V3_SOLVER = SolverRequirement[RecaptchaChallenge, RecaptchaToken]("google.recaptcha-v3")
ENTERPRISE_CHECKBOX_SOLVER = SolverRequirement[RecaptchaChallenge, RecaptchaToken](
    "google.recaptcha-enterprise-checkbox"
)
ENTERPRISE_SCORE_SOLVER = SolverRequirement[RecaptchaChallenge, RecaptchaToken](
    "google.recaptcha-enterprise-score"
)
ENTERPRISE_POLICY_SOLVER = SolverRequirement[RecaptchaChallenge, RecaptchaToken](
    "google.recaptcha-enterprise-policy"
)


def _attribute(html: str, name: str) -> str | None:
    match = re.search(rf"data-{re.escape(name)}\s*=\s*[\"']([^\"']+)", html, re.I)
    return match.group(1) if match else None


def _parser(mode: RecaptchaMode) -> Any:
    def parse(context: Any) -> Any:
        if len(context.response.body) > MAX_HTML_BYTES:
            return Malformed(ValueError("reCAPTCHA HTML exceeds the parser limit"))
        text = context.text
        if text.error is not None or text.value is None:
            return NoMatch()
        html = text.value
        lowered = html.lower()
        marker = "g-recaptcha" in lowered or "grecaptcha.enterprise" in lowered
        if not marker:
            return NoMatch()
        size = _attribute(html, "size")
        is_enterprise = "grecaptcha.enterprise" in lowered or "data-enterprise" in lowered
        if mode is RecaptchaMode.V2_INVISIBLE and size != "invisible":
            return NoMatch()
        if mode is RecaptchaMode.V2_CHECKBOX and (size == "invisible" or is_enterprise):
            return NoMatch()
        if (
            mode
            in {
                RecaptchaMode.ENTERPRISE_CHECKBOX,
                RecaptchaMode.ENTERPRISE_POLICY,
            }
            and not is_enterprise
        ):
            return NoMatch()
        site_key = _attribute(html, "sitekey")
        if not site_key:
            return Malformed(ValueError("reCAPTCHA widget is missing data-sitekey"))
        return ParsedValue(
            RecaptchaChallenge(
                site_key,
                mode,
                context.response.url,
                action=_attribute(html, "action"),
                project=_attribute(html, "project"),
                theme=_attribute(html, "theme"),
                size=size,
                tabindex=_attribute(html, "tabindex"),
                callback=_attribute(html, "callback"),
                badge=_attribute(html, "badge"),
            )
        )

    return callable_parser(RecaptchaChallenge, parse)


def _widget(
    name: str,
    mode: RecaptchaMode,
    requirement: SolverRequirement[RecaptchaChallenge, RecaptchaToken],
    *,
    scope: RequestScope,
    apply: PrivateBindings[RecaptchaToken] | None,
    replay: ReplayPolicy | None,
    browser: bool,
    persistence: ProtectionPersistence | None = None,
    solver: ChallengeSolver[RecaptchaChallenge, RecaptchaToken] | None = None,
) -> PresetChallengePolicy:
    parser = _parser(mode)
    signal = ResponseSignal(
        f"recaptcha.{name}",
        scope,
        RecaptchaChallenge,
        parser,
        priority=10,
        interception=SignalInterception.DEFINITIVE,
    )
    template = ProtectionTemplate(
        PresetId("recaptcha", name),
        1,
        requirement,
        ProtectionCapabilities(BodyAccess.BUFFERED, javascript=True, browser=browser),
    )
    return template.bind_challenge(
        scope=scope,
        signal=signal,
        apply=apply or form_field("g-recaptcha-response"),
        persistence=persistence or per_match(),
        replay=replay or safe_method(max_replays=1),
        solver=solver,
    )


def _before(
    name: str,
    challenge: RecaptchaChallenge,
    requirement: SolverRequirement[RecaptchaChallenge, RecaptchaToken],
    *,
    scope: RequestScope,
    apply: PrivateBindings[RecaptchaToken],
    persistence: ProtectionPersistence,
    browser: bool,
    solver: ChallengeSolver[RecaptchaChallenge, RecaptchaToken] | None = None,
) -> PresetBeforeCallPolicy:
    template = ProtectionTemplate(
        PresetId("recaptcha", name),
        1,
        requirement,
        ProtectionCapabilities(BodyAccess.NONE, javascript=True, browser=browser),
    )
    return template.bind_before(
        scope=scope,
        apply=apply,
        persistence=persistence,
        challenge=challenge,
        solver=solver,
    )


def v2_checkbox(
    *,
    scope: RequestScope,
    solver: ChallengeSolver[RecaptchaChallenge, RecaptchaToken] | None = None,
    apply: PrivateBindings[RecaptchaToken] | None = None,
    replay: ReplayPolicy | None = None,
) -> PresetChallengePolicy:
    return _widget(
        "v2-checkbox",
        RecaptchaMode.V2_CHECKBOX,
        V2_CHECKBOX_SOLVER,
        scope=scope,
        apply=apply,
        replay=replay,
        browser=True,
        solver=solver,
    )


def v2_invisible(
    *,
    scope: RequestScope,
    solver: ChallengeSolver[RecaptchaChallenge, RecaptchaToken] | None = None,
    apply: PrivateBindings[RecaptchaToken] | None = None,
    replay: ReplayPolicy | None = None,
) -> PresetChallengePolicy:
    return _widget(
        "v2-invisible",
        RecaptchaMode.V2_INVISIBLE,
        V2_INVISIBLE_SOLVER,
        scope=scope,
        apply=apply,
        replay=replay,
        browser=True,
        solver=solver,
    )


def v3_action(
    *,
    scope: RequestScope,
    site_key: str,
    action: str,
    solver: ChallengeSolver[RecaptchaChallenge, RecaptchaToken] | None = None,
    apply: PrivateBindings[RecaptchaToken] | None = None,
    persistence: ProtectionPersistence | None = None,
) -> PresetBeforeCallPolicy:
    if not site_key or not action:
        raise ValueError("reCAPTCHA v3 requires non-empty site_key and action")
    return _before(
        f"v3-action:{action}",
        RecaptchaChallenge(site_key, RecaptchaMode.V3_ACTION, action=action),
        V3_SOLVER,
        scope=scope,
        apply=apply or form_field("g-recaptcha-response"),
        persistence=persistence or per_call(),
        browser=False,
        solver=solver,
    )


def enterprise_checkbox(
    *,
    scope: RequestScope,
    solver: ChallengeSolver[RecaptchaChallenge, RecaptchaToken] | None = None,
    apply: PrivateBindings[RecaptchaToken] | None = None,
    replay: ReplayPolicy | None = None,
) -> PresetChallengePolicy:
    return _widget(
        "enterprise-checkbox",
        RecaptchaMode.ENTERPRISE_CHECKBOX,
        ENTERPRISE_CHECKBOX_SOLVER,
        scope=scope,
        apply=apply,
        replay=replay,
        browser=True,
        solver=solver,
    )


def enterprise_score_action(
    *,
    scope: RequestScope,
    project: str,
    site_key: str,
    action: str,
    solver: ChallengeSolver[RecaptchaChallenge, RecaptchaToken] | None = None,
    apply: PrivateBindings[RecaptchaToken] | None = None,
    persistence: ProtectionPersistence | None = None,
) -> PresetBeforeCallPolicy:
    if not project or not site_key or not action:
        raise ValueError("Enterprise score action requires project, site_key and action")
    return _before(
        f"enterprise-score-action:{action}",
        RecaptchaChallenge(
            site_key,
            RecaptchaMode.ENTERPRISE_SCORE,
            action=action,
            project=project,
        ),
        ENTERPRISE_SCORE_SOLVER,
        scope=scope,
        apply=apply or form_field("g-recaptcha-response"),
        persistence=persistence or per_call(),
        browser=False,
        solver=solver,
    )


def enterprise_policy_based_challenge(
    *,
    scope: RequestScope,
    solver: ChallengeSolver[RecaptchaChallenge, RecaptchaToken] | None = None,
    apply: PrivateBindings[RecaptchaToken] | None = None,
    replay: ReplayPolicy | None = None,
) -> PresetChallengePolicy:
    return _widget(
        "enterprise-policy-based-challenge",
        RecaptchaMode.ENTERPRISE_POLICY,
        ENTERPRISE_POLICY_SOLVER,
        scope=scope,
        apply=apply,
        replay=replay,
        browser=True,
        solver=solver,
    )


__all__ = [
    "RecaptchaChallenge",
    "RecaptchaMode",
    "RecaptchaToken",
    "enterprise_checkbox",
    "enterprise_policy_based_challenge",
    "enterprise_score_action",
    "v2_checkbox",
    "v2_invisible",
    "v3_action",
]
