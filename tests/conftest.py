from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._support.client_harness import CLIENT_ADAPTERS, ClientHarness
from tests._support.http_server import LocalHttpServer


def pytest_configure(config: pytest.Config) -> None:
    # ``--basetemp=.test-tmp/pytest`` (pyproject) needs an existing parent on a fresh clone.
    (Path(str(config.rootpath)) / ".test-tmp").mkdir(exist_ok=True)


@pytest.fixture
def http_server() -> Iterator[LocalHttpServer]:
    with LocalHttpServer() as server:
        yield server


@pytest.fixture(params=CLIENT_ADAPTERS, ids=CLIENT_ADAPTERS)
def client_harness(request: pytest.FixtureRequest) -> ClientHarness:
    return ClientHarness(request.param)
