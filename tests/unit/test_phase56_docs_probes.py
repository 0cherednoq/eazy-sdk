"""Executable probes that de-risk the phase-56 documentation examples."""

import pytest

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
