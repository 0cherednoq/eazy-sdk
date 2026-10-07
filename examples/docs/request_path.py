"""Put a typed operation field into a URL path."""

from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk import Http, HttpOperation, Path, SyncApi, op
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class User:
    id: int
    name: str


# docs:example:start
# examples/docs/request_path.py
@dataclass(frozen=True, slots=True, kw_only=True)
class GetUser(HttpOperation[User]):
    __http__ = Http.get("/users/{user_id}")

    user_id: Path[int]


class UsersApi(SyncApi):
    get = op(GetUser)
# docs:example:end


def main() -> None:
    with request_client() as client:
        user = UsersApi(client).get(user_id=42)

    print(f"user: {user.id} {user.name}")


if __name__ == "__main__":
    main()
