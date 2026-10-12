"""``Literal`` in the type of an input value: a ``TypedDict`` field, or the field itself."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict
from urllib.parse import parse_qsl, urlsplit

import pytest
from zapros import Request, Response

from eazy_sdk import Http, HttpOperation, Query, SyncApi, SyncRoot, api_group, op
from eazy_sdk.codecs import ScalarEncodeContext
from eazy_sdk.exceptions import EazySdkError
from eazy_sdk.request import markers
from eazy_sdk.testing import RecordingHandler


class Sort(TypedDict):
    type: Literal["date", "size"]
    descending: Literal[True]


@dataclass(frozen=True, slots=True)
class _Json:
    name: str = "json"

    def encode(self, value: object, context: ScalarEncodeContext) -> str:
        return json.dumps(value, separators=(",", ":"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Listing(HttpOperation[dict[str, bool]]):
    __http__ = Http.get("/items")

    sort: Annotated[Sort, markers.Query(codec=_Json())]
    view: Query[Literal["list", "grid"]] = "list"


class Items(SyncApi):
    list = op(Listing)


class Sdk(SyncRoot):
    items = api_group(Items)


def _ok(request: Request) -> Response:
    return Response(
        200, [("Content-Type", "application/json")], content=b'{"ok": true}', request=request
    )


def test_a_typed_dict_with_literal_fields_is_accepted() -> None:
    handler = RecordingHandler(_ok)
    with Sdk.from_handler(handler=handler, base_url="https://x.test") as sdk:
        sdk.items.list(sort={"type": "size", "descending": True}, view="grid")
    assert parse_qsl(urlsplit(str(handler.last_request.url)).query) == [
        ("sort", '{"type":"size","descending":true}'),
        ("view", "grid"),
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"sort": {"type": "name", "descending": True}},
        # ``1 == True`` in Python; a literal is compared with its type.
        {"sort": {"type": "date", "descending": 1}},
        {"sort": {"type": "date", "descending": True}, "view": "table"},
    ],
)
def test_a_value_outside_the_literal_is_refused_before_the_request(
    arguments: dict[str, object],
) -> None:
    handler = RecordingHandler(_ok)
    with (
        Sdk.from_handler(handler=handler, base_url="https://x.test") as sdk,
        pytest.raises(EazySdkError),
    ):
        sdk.items.list(**arguments)  # type: ignore[arg-type]
    handler.assert_count(0)
