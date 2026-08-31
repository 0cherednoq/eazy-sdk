from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests._support.client_harness import CLIENT_ADAPTERS, ClientHarness
from tests._support.http_server import LocalHttpServer


@pytest.fixture
def http_server() -> Iterator[LocalHttpServer]:
    with LocalHttpServer() as server:
        yield server


@pytest.fixture(params=CLIENT_ADAPTERS, ids=CLIENT_ADAPTERS)
def client_harness(request: pytest.FixtureRequest) -> ClientHarness:
    return ClientHarness(request.param)
