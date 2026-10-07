"""Solve a captcha on the password step and replay that step once."""

from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk.protection import (
    Guard,
    GuardSolution,
    SolveContext,
    host,
    rejected_before_execution,
)
from eazy_sdk.response import ResponseContext

# region docs: http-captcha-guard
# examples/mail/http/captcha_probe.py
@dataclass(frozen=True, slots=True)
class LoginCaptcha:
    site_key: str


class LoginCaptchaGuard(Guard[LoginCaptcha]):
    """Apply a teaching clearance cookie, then repeat the rejected login step."""

    scope = host("mail.example")
    replay = rejected_before_execution(max_replays=1)

    def __init__(self) -> None:
        self.solved: list[str] = []

    def detect(self, response: ResponseContext[object]) -> LoginCaptcha | None:
        if response.response.status_code != 403:
            return None
        document = response.json.value
        if not isinstance(document, dict) or document.get("kind") != "captcha":
            return None
        site_key = document.get("site_key")
        return LoginCaptcha(site_key) if isinstance(site_key, str) else None

    def solve(self, challenge: LoginCaptcha, context: SolveContext) -> GuardSolution:
        self.solved.append(challenge.site_key)
        return self.solution(cookies={"login_clearance": "solved"})
# endregion docs: http-captcha-guard
