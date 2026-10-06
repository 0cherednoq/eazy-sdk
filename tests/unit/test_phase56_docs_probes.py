"""Executable probes that de-risk the phase-56 documentation examples."""

import pytest

from examples.mail.http.captcha_probe import main as captcha_main
from examples.mail.http.login_probe import main


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
