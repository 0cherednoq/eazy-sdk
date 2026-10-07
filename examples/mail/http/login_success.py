"""Run the successful three-step HTTP login from the tutorial."""

from __future__ import annotations

from examples.mail.http._client import mail_client
from examples.mail.http.login_probe import VALID_INPUT, LoginApi


# region docs: http-login-flow
# examples/mail/http/login_success.py
def login(login_api: LoginApi) -> str:
    password_step = login_api.identify(email="ada@mail.example")
    otp_step = login_api.password(
        login_id=password_step.login_id,
        password=VALID_INPUT,
    )
    session = login_api.otp(login_id=otp_step.login_id, code="123456")
    return session.access_token
# endregion docs: http-login-flow


def main() -> None:
    with mail_client() as client:
        token = login(LoginApi(client))
    print(f"session: {token}")


if __name__ == "__main__":
    main()
