"""A router of another transport composes itself inside a root.

The root decides the client (``bind(Router, client=...)``) and nothing else: the
router's service attributes, allowlists and address are its own vocabulary, so the
root neither reads them nor resolves them as HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Self, cast

import pytest

from eazy_sdk import AsyncApi, AsyncRoot, Http, HttpOperation, SyncRoot, api_group, bind, op
from eazy_sdk.clients import AsyncClient
from eazy_sdk.testing import AsyncRecordingHandler

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class Ping(HttpOperation[dict[str, object]]):
    __http__ = Http.get("/ping")


class Http_(AsyncApi):
    ping = op(Ping)


class Foreign:
    """A router the root knows nothing about, except that it composes itself."""

    composed: ClassVar[list[object]] = []

    def __init__(self, client: object) -> None:
        self.client = client

    @classmethod
    def __compose__(cls, client: object) -> Self:
        cls.composed.append(client)
        return cls(client)


class Sdk(AsyncRoot):
    http = api_group(Http_)
    foreign = api_group(Foreign)


def _http() -> AsyncClient:
    return AsyncClient(base_url="https://x.example", handler=AsyncRecordingHandler(status=200))


def test_root_hands_the_bound_client_to_a_foreign_router() -> None:
    marker = object()

    sdk = Sdk(_http(), bindings=(bind(Foreign, client=marker),))

    assert sdk.foreign.client is marker
    assert sdk.foreign is sdk.foreign, "a group is built once per root"


def test_root_falls_back_to_the_default_client_for_a_foreign_router() -> None:
    http = _http()

    assert Sdk(http).foreign.client is http


def test_foreign_router_is_not_checked_as_an_http_router() -> None:
    """No service defaults are read and no operation is resolved: it has neither."""

    class Sync(SyncRoot):
        foreign = api_group(Foreign)

    assert Sync.foreign.api_type is Foreign


def test_api_group_still_refuses_an_arbitrary_class() -> None:
    class Plain:
        pass

    with pytest.raises(TypeError, match="composes"):
        api_group(cast(Any, Plain))
