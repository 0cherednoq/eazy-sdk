"""Project flat public inputs into a nested service-owned JSON document."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from eazy_sdk import BodyProjection, Http, HttpOperation, SyncApi, op
from eazy_sdk.request import Body
from examples.docs.request_site import request_client


class AccountWire(TypedDict):
    login: str
    email: str


class RegistrationWire(TypedDict):
    account: AccountWire
    client: str


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    login: str


# docs:projection:start
# examples/docs/request_projection.py
@dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUser(HttpOperation[Account]):
    __http__ = Http.post(
        "/register",
        success={201: Account},
        projection=BodyProjection(
            target=RegistrationWire,
            using=lambda source: {
                "account": {"login": source.login, "email": source.email},
                "client": "python",
            },
            encoding=Body.json(),
            name="registration-v1",
        ),
    )

    login: str
    email: str


class RegistrationApi(SyncApi):
    register = op(RegisterUser)
# docs:projection:end


def main() -> None:
    with request_client() as client:
        account = RegistrationApi(client).register(
            login="ada",
            email="ada@mail.example",
        )

    print(f"registered: {account.login} as {account.id}")


if __name__ == "__main__":
    main()
