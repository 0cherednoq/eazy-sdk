from __future__ import annotations

import httpx
import pytest

from tests._support.http_server import LocalHttpServer
from tests._support.zapros_clients import client_from_httpx

pytestmark = [pytest.mark.regression, pytest.mark.integration]


def test_duplicate_existing_url_query_is_rejected_before_network(
    http_server: LocalHttpServer,
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client, pytest.raises(ValueError, match="duplicate raw query"):
        client.get(
            "/echo?existing=%7E&duplicate=first&duplicate=second",
            params={"added": "a b"},
        )
    assert http_server.exchanges == ()
