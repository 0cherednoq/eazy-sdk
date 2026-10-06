"""Executable probes that de-risk the phase-56 documentation examples."""

import json
from dataclasses import dataclass, field

import httpx
import pytest

from examples.mail.browser.login_probe import Identify, LoginPortal, Mailbox, OtpStep, PasswordStep
from examples.mail.http.captcha_probe import main as captcha_main
from examples.mail.http.login_probe import main
from examples.mail.site import handle_httpx, intercept_page


def test_http_login_probe_prints_all_six_outcomes(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "wrong password: WrongPassword",
        "account not found: AccountNotFound",
        "account blocked: AccountBlocked",
        "second factor: OtpStep",
        "wrong code: WrongCode",
        "captcha: CaptchaRequired",
    ]


def test_http_captcha_probe_replays_the_password_step(capsys: pytest.CaptureFixture[str]) -> None:
    captcha_main()
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "challenge solved: mail-login",
        "password requests: 2",
        "replay cookie: login_clearance=solved",
        "next step: OtpStep",
    ]


def test_browser_login_probe_declares_three_typed_steps() -> None:
    assert LoginPortal.identify.operation is Identify
    identify_outcomes = Identify.__browser__.outcomes
    password_outcomes = LoginPortal.password.operation.__browser__.outcomes
    otp_outcomes = LoginPortal.otp.operation.__browser__.outcomes
    assert identify_outcomes is not None
    assert password_outcomes is not None
    assert otp_outcomes is not None
    assert identify_outcomes.cases[0].to is PasswordStep
    assert password_outcomes.cases[0].to is OtpStep
    assert otp_outcomes.cases[0].to is Mailbox


@dataclass(slots=True)
class _PageRequest:
    method: str
    url: str
    headers: dict[str, str]
    post_data_buffer: bytes | None


@dataclass(slots=True)
class _PageRoute:
    request: _PageRequest
    response: tuple[int, dict[str, str], bytes] | None = field(default=None, init=False)

    async def fulfill(self, *, status: int, headers: dict[str, str], body: bytes) -> None:
        self.response = (status, headers, body)


@pytest.mark.asyncio
async def test_one_teaching_site_serves_http_and_page_interception() -> None:
    body = json.dumps({"email": "ada@mail.example"}).encode()
    http_response = handle_httpx(
        httpx.Request("POST", "https://mail.example/login/identify", content=body)
    )
    route = _PageRoute(
        _PageRequest(
            method="POST",
            url="https://mail.example/login/identify",
            headers={"content-type": "application/json"},
            post_data_buffer=body,
        )
    )

    await intercept_page(route)

    assert route.response is not None
    status, headers, intercepted_body = route.response
    assert status == http_response.status_code == 200
    assert headers["content-type"] == http_response.headers["content-type"]
    assert intercepted_body == http_response.content
