from __future__ import annotations

import httpx
import pytest

from eazy_sdk.clients import CallOptions
from tests._support.http_server import LocalHttpServer
from tests._support.zapros_clients import client_from_httpx

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_get_redirects_are_managed_by_eazy_sdk(http_server: LocalHttpServer, status: int) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={}, follow_redirects=True)
    with client_from_httpx(raw) as client:
        response = client.request("GET",
            f"/redirect/{status}",
            options=CallOptions(max_attempts=2, max_redirects=1),
        )
    assert response.status_code == 200
    assert [exchange.target for exchange in http_server.exchanges] == [
        f"/redirect/{status}",
        "/echo",
    ]
    assert [exchange.method for exchange in http_server.exchanges] == ["GET", "GET"]


@pytest.mark.parametrize("status", [307, 308])
def test_strict_redirects_preserve_post_method_and_body(
    http_server: LocalHttpServer, status: int
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client:
        response = client.request("POST",
            f"/redirect/{status}",
            content=b"payload",
            options=CallOptions(max_attempts=2, max_redirects=1),
        )
    assert response.status_code == 200
    assert [(item.method, item.body) for item in http_server.exchanges] == [
        ("POST", b"payload"),
        ("POST", b"payload"),
    ]


@pytest.mark.parametrize("status", [301, 302, 303])
def test_post_redirects_switch_to_get_and_drop_the_body(
    http_server: LocalHttpServer, status: int
) -> None:
    raw = httpx.Client(base_url=http_server.url, headers={}, cookies={})
    with client_from_httpx(raw) as client:
        response = client.request("POST",
            f"/redirect/{status}",
            content=b"payload",
            options=CallOptions(max_attempts=2, max_redirects=1),
        )
    assert response.status_code == 200
    assert [(item.method, item.body) for item in http_server.exchanges] == [
        ("POST", b"payload"),
        ("GET", b""),
    ]
