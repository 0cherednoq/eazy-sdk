"""An address that repeats a query name is sent as it was written.

A server may redirect to ``/inbox/?from=login&from=octavius``; a browser follows it. The Zapros
client merges a URL's query through a mapping and keeps one value per name, so the prepared
address has to be put back before the handler sees the request.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from zapros import Request, Response

from eazy_sdk import (
    AsyncApi,
    AsyncRoot,
    ClientConfig,
    Http,
    HttpOperation,
    Query,
    Resilience,
    SyncApi,
    SyncRoot,
    api_group,
    op,
)
from eazy_sdk.core.errors import PlanError
from eazy_sdk.testing import AsyncRecordingHandler, RecordingHandler

BASE = "https://mail.example.test"
TARGET = f"{BASE}/inbox/?authid=a1&from=login&from=octavius&from=octavius"


@dataclass(frozen=True, slots=True, kw_only=True)
class Open(HttpOperation[dict[str, bool]]):
    __http__ = Http.get("/open")


@dataclass(frozen=True, slots=True, kw_only=True)
class Listed(HttpOperation[dict[str, bool]]):
    """The path the author wrote already repeats a name."""

    __http__ = Http.get("/items?tag=a&tag=b")

    page: Query[int] = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class Clashing(HttpOperation[dict[str, bool]]):
    """A declared parameter collides with a name of the path."""

    __http__ = Http.get("/items?tag=a")

    tag: Query[str]


class Pages(SyncApi):
    open = op(Open)
    listed = op(Listed)
    clashing = op(Clashing)


class Sdk(SyncRoot):
    pages = api_group(Pages)


class AsyncPages(AsyncApi):
    open = op(Open)


class AsyncSdk(AsyncRoot):
    pages = api_group(AsyncPages)


def _answer(request: Request) -> Response:
    if str(request.url).endswith("/open"):
        return Response(302, [("Location", TARGET)], content=b"", request=request)
    return Response(
        200, [("Content-Type", "application/json")], content=b'{"ok": true}', request=request
    )


_FOLLOW = ClientConfig(resilience=Resilience(max_redirects=2))


def test_a_redirect_target_with_a_repeated_name_is_followed_as_written() -> None:
    handler = RecordingHandler(_answer)
    with Sdk.from_handler(handler=handler, base_url=BASE, config=_FOLLOW) as sdk:
        assert sdk.pages.open() == {"ok": True}
    assert [str(request.url) for request in handler.requests] == [f"{BASE}/open", TARGET]


async def test_the_async_client_follows_it_the_same_way() -> None:
    handler = AsyncRecordingHandler(_answer)
    async with AsyncSdk.from_handler(handler=handler, base_url=BASE, config=_FOLLOW) as sdk:
        assert await sdk.pages.open() == {"ok": True}
    assert [str(request.url) for request in handler.requests] == [f"{BASE}/open", TARGET]


def test_a_repeated_name_in_the_declared_path_is_kept_with_generated_parameters() -> None:
    handler = RecordingHandler(_answer)
    with Sdk.from_handler(handler=handler, base_url=BASE) as sdk:
        sdk.pages.listed(page=3)
    assert str(handler.last_request.url) == f"{BASE}/items?tag=a&tag=b&page=3"


def test_a_declared_parameter_still_may_not_repeat_a_name_of_the_address() -> None:
    handler = RecordingHandler(_answer)
    with (
        Sdk.from_handler(handler=handler, base_url=BASE) as sdk,
        pytest.raises(PlanError, match="duplicate query wire name 'tag'"),
    ):
        sdk.pages.clashing(tag="b")
    handler.assert_count(0)
