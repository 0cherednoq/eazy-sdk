"""Run all six documented outcomes of the HTTP login."""

from __future__ import annotations

from collections.abc import Callable

from eazy_sdk.response import ApiError

from examples.mail.http._client import mail_client
from examples.mail.http.login_probe import VALID_INPUT, WRONG_INPUT, LoginApi


def _print_error(label: str, call: Callable[[], object]) -> None:
    try:
        call()
    except ApiError as error:
        print(f"{label}: {type(error).__name__}")


def main() -> None:
    with mail_client() as client:
        login = LoginApi(client)

        password_step = login.identify(email="ada@mail.example")
        _print_error(
            "wrong password",
            lambda: login.password(login_id=password_step.login_id, password=WRONG_INPUT),
        )
        _print_error("account not found", lambda: login.identify(email="missing@mail.example"))
        _print_error("account blocked", lambda: login.identify(email="blocked@mail.example"))

        otp_step = login.password(login_id=password_step.login_id, password=VALID_INPUT)
        print(f"second factor: {type(otp_step).__name__}")
        _print_error(
            "wrong code",
            lambda: login.otp(login_id=otp_step.login_id, code="000000"),
        )
        _print_error(
            "captcha",
            lambda: login.password(
                login_id=login.identify(email="captcha@mail.example").login_id,
                password=VALID_INPUT,
            ),
        )


if __name__ == "__main__":
    main()
