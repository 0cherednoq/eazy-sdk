"""Solve a captcha on the password step and replay that step once."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from eazy_sdk import Client, ClientConfig, Security
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.protection import (
    Guard,
    GuardSolution,
    SolveContext,
    host,
    rejected_before_execution,
)
from eazy_sdk.response import ResponseContext

from examples.mail.site import handle_httpx

from .login_probe import LoginApi, VALID_INPUT


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


@dataclass(slots=True)
class CaptchaSite:
    password_requests: list[str | None] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/login/password":
            return handle_httpx(request)
        cookie = request.headers.get("Cookie")
        self.password_requests.append(cookie)
        if "login_clearance=solved" not in (cookie or ""):
            return httpx.Response(
                403,
                json={"kind": "captcha", "site_key": "mail-login"},
            )
        return handle_httpx(request)


def main() -> None:
    site = CaptchaSite()
    guard = LoginCaptchaGuard()
    raw = httpx.Client(
        transport=httpx.MockTransport(site),
        headers={},
        cookies={},
    )
    with Client(
        handler=HttpxHandler(raw, owns_client=True),
        config=ClientConfig(security=Security.of(guard)),
    ) as client:
        login = LoginApi(client)
        password_step = login.identify(email="ada@mail.example")
        next_step = login.password(login_id=password_step.login_id, password=VALID_INPUT)

    print(f"challenge solved: {guard.solved[0]}")
    print(f"password requests: {len(site.password_requests)}")
    print(f"replay cookie: {site.password_requests[1]}")
    print(f"next step: {type(next_step).__name__}")


if __name__ == "__main__":
    main()
