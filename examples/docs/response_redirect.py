"""Read the outcome of a call from the address its redirect points to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk import Http, HttpOperation, SyncApi, op
from eazy_sdk.request import Form
from eazy_sdk.response import ApiError, Const, Location
from examples.docs.redirect_site import MAILBOX, PASSWORD, redirect_client


# docs:example:start
# examples/docs/response_redirect.py
@dataclass(frozen=True, slots=True)
class SignedIn:
    url: Annotated[str, Location(path="/inbox*")]


@dataclass(frozen=True, slots=True)
class CodeRequired:
    url: Annotated[str, Location(path="/cgi-bin/secstep*")]


@dataclass(frozen=True, slots=True)
class Banned:
    errno: Annotated[str, Location.query("errno"), Const("25")]


@dataclass(frozen=True, slots=True)
class Rejected:
    fail: Annotated[str, Location.query("fail")]


class AccountBanned(ApiError[Banned]):
    pass


class InvalidCredentials(ApiError[Rejected]):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitCredentials(HttpOperation[SignedIn | CodeRequired]):
    __http__ = Http.post(
        "/cgi-bin/auth",
        success={302: [SignedIn, CodeRequired]},
        errors={302: [AccountBanned, InvalidCredentials]},
    )

    username: Form[str]
    password: Form[str]


class LoginApi(SyncApi):
    submit = op(SubmitCredentials)


# docs:example:end


def main() -> None:
    attempts = (
        (MAILBOX, PASSWORD),
        ("otp@mail.example", PASSWORD),
        ("banned@mail.example", PASSWORD),
        (MAILBOX, "wrong-password"),
    )
    with redirect_client() as client:
        login = LoginApi(client)
        for username, password in attempts:
            try:
                outcome = login.submit(username=username, password=password)
            except AccountBanned as error:
                print(f"{username}: banned, errno {error.error.errno}")
            except InvalidCredentials as error:
                print(f"{username}: rejected, fail {error.error.fail}")
            else:
                print(f"{username}: {type(outcome).__name__} {outcome.url}")


if __name__ == "__main__":
    main()
