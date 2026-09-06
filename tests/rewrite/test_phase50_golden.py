"""Phase 50 golden tests: the wire bytes do not depend on who wrote the operation.

Decorator versus class, dataclass versus Pydantic versus msgspec, ``UNSET`` versus "not passed":
each pair prepares the same request, compared as a whole ``PreparedCall`` — method, url,
headers in order, body — never field by field.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast

import msgspec
import pytest
from pydantic import BaseModel, ConfigDict, Field
from zapros import BaseHandler, Request, Response

from eazy_sdk import (
    UNSET,
    Client,
    Http,
    HttpOperation,
    JsonField,
    Omittable,
    Path,
    Query,
    SyncApi,
    api,
    op,
)
from eazy_sdk.preparation import PreparedCall
from eazy_sdk.request import markers
from eazy_sdk.response import ApiError


class User(BaseModel):
    name: str


class Problem(BaseModel):
    code: str


class UserNotFound(ApiError[Problem]):
    pass


class _Handler(BaseHandler):
    def handle(self, request: Request) -> Response:
        return Response(
            200, [("Content-Type", "application/json")], content=b'{"name":"Ada"}', request=request
        )

    def close(self) -> None:
        return None


def _client() -> Client:
    return Client(base_url="https://api.example", handler=_Handler())


def _golden(call: PreparedCall) -> tuple[object, ...]:
    return (
        call.method,
        call.url,
        call.target,
        tuple((item.name, item.value) for item in call.headers),
        tuple((item.name, item.value) for item in call.cookies),
        call.body,
        call.encoded_body,
    )


# --- decorator ≡ class -------------------------------------------------------------


class UsersApi(SyncApi):
    @api.post("/users/{user_id}", errors={404: UserNotFound})
    def update_user(
        self,
        *,
        user_id: Path[int],
        locale: Query[str] = "en",
        trace: Annotated[str, markers.Header("X-Trace")] = "t-1",
        name: JsonField[str],
        nickname: JsonField[str] | None = None,
    ) -> User:
        raise NotImplementedError


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateUser(HttpOperation[User]):
    __http__ = Http.post("/users/{user_id}", operation_id="update_user", errors={404: UserNotFound})
    user_id: Path[int]
    locale: Query[str] = "en"
    trace: Annotated[str, markers.Header("X-Trace")] = "t-1"
    name: JsonField[str]
    nickname: JsonField[Omittable[str]] = UNSET


class UsersApi2(SyncApi):
    update_user = op(UpdateUser)


def test_decorator_and_class_prepare_identical_bytes() -> None:
    """I2: the decorator synthesizes exactly the class an author would have written."""

    client = _client()
    decorated = UsersApi(client).update_user
    declared = UsersApi2(client).update_user
    calls: tuple[dict[str, Any], ...] = (
        {"user_id": 1, "name": "Ada"},
        {"user_id": 2, "name": "Ada", "locale": "ru", "trace": "t-9", "nickname": "ada"},
    )
    for kwargs in calls:
        assert _golden(decorated.prepare(**kwargs)) == _golden(declared.prepare(**kwargs))
    assert UsersApi.update_user.Operation.__http__ == UpdateUser.__http__
    assert UsersApi.update_user.declaration.operation_id == "update_user"
    assert decorated.prepare(user_id=1, name="Ada").body == (("name", "Ada"),)


# --- dataclass ≡ pydantic ≡ msgspec ---------------------------------------------------


def _dataclass_form() -> type[HttpOperation[User]]:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class SearchUsers(HttpOperation[User]):
        __http__ = Http.post("/search")
        page: Query[int] = 1
        per_page: Annotated[int, markers.Query("perPage")] = 20
        locale: Annotated[str, markers.Header("Accept-Language")] = "en"
        term: Annotated[str, markers.JsonField("q")]
        limit: JsonField[Omittable[int]] = UNSET

    return SearchUsers


def _pydantic_form() -> type[HttpOperation[User]]:
    class SearchUsers(BaseModel, HttpOperation[User]):
        model_config = ConfigDict(frozen=True, serialize_by_alias=True)
        __http__ = Http.post("/search")
        page: Query[int] = 1
        per_page: Query[int] = Field(20, serialization_alias="perPage")
        locale: Annotated[str, markers.Header()] = Field(
            "en", serialization_alias="Accept-Language"
        )
        term: Annotated[str, markers.JsonField()] = Field(serialization_alias="q")
        limit: JsonField[Omittable[int]] = UNSET

    return SearchUsers


def _msgspec_form() -> type[HttpOperation[User]]:
    class SearchUsers(msgspec.Struct, HttpOperation[User], frozen=True, kw_only=True):
        __http__ = Http.post("/search")
        page: Query[int] = 1
        per_page: Query[int] = msgspec.field(default=20, name="perPage")
        locale: Annotated[str, markers.Header()] = msgspec.field(
            default="en", name="Accept-Language"
        )
        term: Annotated[str, markers.JsonField()] = msgspec.field(name="q")
        limit: JsonField[Omittable[int]] = UNSET

    return SearchUsers


@pytest.mark.parametrize(
    "factory",
    [_pydantic_form, _msgspec_form],
    ids=["pydantic", "msgspec"],
)
def test_three_libraries_prepare_identical_bytes(
    factory: Callable[[], type[HttpOperation[User]]],
) -> None:
    """I3: the same wire names give the same bytes, whichever library declares the class."""

    class Reference(SyncApi):
        search = op(cast(Any, _dataclass_form()))

    class Candidate(SyncApi):
        search = op(cast(Any, factory()))

    client = _client()
    calls: tuple[dict[str, Any], ...] = (
        {"term": "ada"},
        {"term": "ada", "page": 2, "per_page": 5, "limit": 3},
    )
    for kwargs in calls:
        assert _golden(Candidate(client).search.prepare(**kwargs)) == _golden(
            Reference(client).search.prepare(**kwargs)
        )


def test_unset_equals_not_passed() -> None:
    """I5: ``UNSET`` and an omitted argument are one request, ``None`` is another."""

    class Reference(SyncApi):
        search = op(cast(Any, _dataclass_form()))

    search = Reference(_client()).search
    assert _golden(search.prepare(term="a", limit=UNSET)) == _golden(search.prepare(term="a"))
    assert search.prepare(term="a").body == (("q", "a"),)
