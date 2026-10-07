"""Solve the HTTP captcha and replay the rejected password request."""

from __future__ import annotations

from eazy_sdk import ClientConfig, Security

from examples.mail.http._client import configured_mail_client
from examples.mail.http.captcha_probe import LoginCaptchaGuard
from examples.mail.http.login_probe import VALID_INPUT, LoginApi
from examples.mail.site import MailSite


# region docs: http-captcha-flow
# examples/mail/http/captcha.py
def login_after_captcha() -> tuple[str, int, str | None, str]:
    site = MailSite()
    guard = LoginCaptchaGuard()
    config = ClientConfig(security=Security.of(guard))
    with configured_mail_client(site, config) as client:
        login = LoginApi(client)
        password = login.identify(email="captcha@mail.example")
        otp = login.password(login_id=password.login_id, password=VALID_INPUT)
    return (
        guard.solved[0],
        len(site.state.password_cookies),
        site.state.password_cookies[1],
        type(otp).__name__,
    )
# endregion docs: http-captcha-flow


def main() -> None:
    challenge, requests, cookie, next_step = login_after_captcha()
    print(f"challenge solved: {challenge}")
    print(f"password requests: {requests}")
    print(f"replay cookie: {cookie}")
    print(f"next step: {next_step}")


if __name__ == "__main__":
    main()
