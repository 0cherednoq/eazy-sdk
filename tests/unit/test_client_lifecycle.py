from __future__ import annotations

import asyncio

import httpx
import pytest

from tests._support.zapros_clients import client_from_httpx

pytestmark = pytest.mark.unit


def response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"ok", request=request)


def test_sync_context_manager_closes_after_success_and_close_is_idempotent() -> None:
    raw = httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(response),
        headers={},
        cookies={},
    )
    with client_from_httpx(raw) as client:
        assert client.request("GET", "/ok").body == b"ok"
        assert not raw.is_closed
    assert raw.is_closed
    client.close()
    assert raw.is_closed


def test_sync_context_manager_closes_when_user_code_raises() -> None:
    raw = httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(response),
    )
    with pytest.raises(RuntimeError, match="user failure"), client_from_httpx(raw):
        raise RuntimeError("user failure")
    assert raw.is_closed


def test_sync_request_after_close_is_rejected_before_transport() -> None:
    raw = httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(response),
    )
    client = client_from_httpx(raw)
    client.close()
    with pytest.raises(RuntimeError, match="Client is closed"):
        client.request("GET", "/closed")


@pytest.mark.asyncio
async def test_async_context_manager_closes_after_exception_and_aclose_is_idempotent() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response(request)

    raw = httpx.AsyncClient(
        base_url="https://api.test",
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    with pytest.raises(RuntimeError, match="user failure"):
        async with client_from_httpx(raw) as client:
            assert (await client.request("GET", "/ok")).body == b"ok"
            raise RuntimeError("user failure")
    assert raw.is_closed
    await client.aclose()
    assert raw.is_closed


@pytest.mark.asyncio
async def test_async_cancellation_stops_the_attempt_and_client_remains_usable() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/wait":
            started.set()
            await release.wait()
        return response(request)

    raw = httpx.AsyncClient(
        base_url="https://api.test",
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    async with client_from_httpx(raw) as client:
        task = asyncio.create_task(client.request("GET", "/wait"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await client.request("GET", "/ok")).body == b"ok"
    assert calls == ["/wait", "/ok"]
    assert raw.is_closed
