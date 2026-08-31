from __future__ import annotations

from typing import Any, cast, overload

import httpx
import requests
from curl_cffi import requests as curl_requests

from eazy_sdk import AsyncClient, Client, ClientConfig
from eazy_sdk.handlers.curl_cffi import (
    AsyncCurlCffiZaprosHandler,
    CurlCffiZaprosHandler,
)
from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler
from eazy_sdk.handlers.requests import RequestsHandler


@overload
def client_from_httpx(
    raw: httpx.Client,
    *,
    config: ClientConfig | None = None,
) -> Client: ...


@overload
def client_from_httpx(
    raw: httpx.AsyncClient,
    *,
    config: ClientConfig | None = None,
) -> AsyncClient: ...


def client_from_httpx(
    raw: httpx.Client | httpx.AsyncClient,
    *,
    config: ClientConfig | None = None,
) -> Client | AsyncClient:
    base_url = str(raw.base_url)
    if isinstance(raw, httpx.AsyncClient):
        return AsyncClient(
            base_url=base_url,
            handler=AsyncHttpxHandler(raw, owns_client=True),
            config=config,
        )
    return Client(
        base_url=base_url,
        handler=HttpxHandler(raw, owns_client=True),
        config=config,
    )


def client_from_requests(
    raw: requests.Session,
    *,
    config: ClientConfig | None = None,
    base_url: str = "",
) -> Client:
    return Client(
        base_url=base_url,
        handler=RequestsHandler(raw, owns_session=True),
        config=config,
    )


@overload
def client_from_curl_cffi(
    raw: curl_requests.Session,
    *,
    config: ClientConfig | None = None,
    base_url: str = "",
    impersonate: str | None = None,
) -> Client: ...


@overload
def client_from_curl_cffi(
    raw: curl_requests.AsyncSession,
    *,
    config: ClientConfig | None = None,
    base_url: str = "",
    impersonate: str | None = None,
) -> AsyncClient: ...


def client_from_curl_cffi(
    raw: object,
    *,
    config: ClientConfig | None = None,
    base_url: str = "",
    impersonate: str | None = None,
) -> Client | AsyncClient:
    if isinstance(raw, curl_requests.AsyncSession):
        return AsyncClient(
            base_url=base_url,
            handler=AsyncCurlCffiZaprosHandler(
                raw,
                impersonate=cast(Any, impersonate),
                owns_session=True,
            ),
            config=config,
        )
    if isinstance(raw, curl_requests.Session):
        return Client(
            base_url=base_url,
            handler=CurlCffiZaprosHandler(
                raw,
                impersonate=cast(Any, impersonate),
                owns_session=True,
            ),
            config=config,
        )
    raise TypeError("expected curl_cffi Session or AsyncSession")


__all__ = ["client_from_curl_cffi", "client_from_httpx", "client_from_requests"]
