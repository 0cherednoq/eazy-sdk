"""Send required and omitted query parameters."""

from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk import UNSET, Http, HttpOperation, Omittable, Query, SyncApi, op
from examples.docs.request_site import request_client


@dataclass(frozen=True, slots=True)
class User:
    id: int
    name: str
    query: str


# docs:example:start
# examples/docs/request_query.py
@dataclass(frozen=True, slots=True, kw_only=True)
class SearchUsers(HttpOperation[list[User]]):
    __http__ = Http.get("/users")

    q: Query[str]
    limit: Query[Omittable[int]] = UNSET


class UsersApi(SyncApi):
    search = op(SearchUsers)
# docs:example:end


def main() -> None:
    with request_client() as client:
        users = UsersApi(client).search(q="ada")

    print(f"query: {users[0].query}; result: {users[0].name}")


if __name__ == "__main__":
    main()
