"""Encode operation fields as an HTML form body."""

from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk import UNSET, Http, HttpOperation, Omittable, SyncApi, op
from eazy_sdk.request import Form
from examples.docs.request_site import request_client

DEMO_CREDENTIALS = ("ada@mail.example", "mail-password")


@dataclass(frozen=True, slots=True)
class Session:
    account: str
    access_token: str


# docs:example:start
# examples/docs/request_form.py
@dataclass(frozen=True, slots=True, kw_only=True)
class Login(HttpOperation[Session]):
    __http__ = Http.post("/login")

    email: Form[str]
    password: Form[str]
    remember: Form[Omittable[bool]] = UNSET


class AuthApi(SyncApi):
    login = op(Login)
# docs:example:end


def main() -> None:
    with request_client() as client:
        session = AuthApi(client).login(
            email=DEMO_CREDENTIALS[0],
            password=DEMO_CREDENTIALS[1],
        )

    print(f"session: {session.account} {session.access_token}")


if __name__ == "__main__":
    main()
