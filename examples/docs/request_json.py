"""Encode operation fields as a JSON object."""

from __future__ import annotations

from dataclasses import dataclass, field

from eazy_sdk import UNSET, Http, HttpOperation, JsonField, Omittable, SyncApi, op
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class User:
    id: int
    name: str


# docs:example:start
# examples/docs/request_json.py
@dataclass(frozen=True, slots=True, kw_only=True)
class CreateUser(HttpOperation[User]):
    __http__ = Http.post("/users", success={201: User})

    name: JsonField[str]
    tags: JsonField[list[str]] = field(default_factory=list)
    note: JsonField[Omittable[str]] = UNSET


class UsersApi(SyncApi):
    create = op(CreateUser)
# docs:example:end


def main() -> None:
    with request_client() as client:
        user = UsersApi(client).create(name="Ada", tags=["admin"])

    print(f"created: {user.id} {user.name}")


if __name__ == "__main__":
    main()
