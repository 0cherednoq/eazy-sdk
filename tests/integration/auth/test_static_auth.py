from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from pytest_httpserver import HTTPServer

from eazy_sdk import AsyncApi, ClientConfig, SyncApi, api
from eazy_sdk.auth import (
    ApiKeyScheme,
    Auth,
    AuthScheme,
    BasicScheme,
    BearerScheme,
    CookieScheme,
    all_of,
    any_of,
)
from eazy_sdk.ext import AuthProviderIdentity, AuthProviders, StaticAuthProvider
from eazy_sdk.response import NormalizedResponse, Responses
from eazy_sdk.response.normalized import cast_headers
from tests._support.zapros_clients import client_from_httpx

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class StaticAuthCase:
    name: str
    scheme: AuthScheme[Any]
    credential: object
    expected_headers: dict[str, str]
    expected_query: dict[str, str] | None = None


STATIC_CASES = (
    StaticAuthCase(
        "basic",
        BasicScheme(),
        ("museum-user", "correct horse battery staple"),
        {
            "Authorization": "Basic "
            + base64.b64encode(b"museum-user:correct horse battery staple").decode("ascii")
        },
    ),
    StaticAuthCase(
        "bearer",
        BearerScheme(),
        "opaque-access-token",
        {"Authorization": "Bearer opaque-access-token"},
    ),
    StaticAuthCase(
        "jwt-bearer",
        BearerScheme("jwt"),
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature",
        {"Authorization": ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature")},
    ),
    StaticAuthCase(
        "api-key-header",
        ApiKeyScheme.header("X-API-Key"),
        "header-secret",
        {"X-API-Key": "header-secret"},
    ),
    StaticAuthCase(
        "api-key-query",
        ApiKeyScheme.query("api_key"),
        "query-secret",
        {},
        {"api_key": "query-secret"},
    ),
    StaticAuthCase(
        "cookie-session",
        CookieScheme("session_id"),
        "session-secret",
        {"Cookie": "session_id=session-secret"},
    ),
)


@dataclass(frozen=True, slots=True)
class _RawOperation:
    async_api: type[AsyncApi]
    sync_api: type[SyncApi]


def _raw_operation(
    security: object,
    *,
    operation_id: str = "protectedResource",
) -> _RawOperation:
    class AsyncProtected(AsyncApi):
        @api.get(
            "/protected",
            operation_id=operation_id,
            responses=Responses(success=()),
            security=security,
            raw_response=True,
        )
        async def protected(self) -> NormalizedResponse[object]:
            raise NotImplementedError

    class SyncProtected(SyncApi):
        @api.get(
            "/protected",
            operation_id=operation_id,
            responses=Responses(success=()),
            security=security,
            raw_response=True,
        )
        def protected(self) -> NormalizedResponse[object]:
            raise NotImplementedError

    return _RawOperation(AsyncProtected, SyncProtected)


def _providers(*entries: tuple[AuthScheme[Any], object]) -> Auth:
    if not entries:
        raise ValueError("at least one auth entry is required")
    providers = AuthProviders()
    for index, (scheme, credential) in enumerate(entries):
        providers.register(
            scheme,
            StaticAuthProvider(
                scheme,
                credential,
                AuthProviderIdentity(f"localhost-{index}"),
            ),
        )
    return Auth._bind(entries[0][0], providers)


async def _execute(
    *,
    async_client: bool,
    base_url: str,
    operation: _RawOperation,
    providers: Auth,
) -> NormalizedResponse[object]:
    if async_client:
        raw_async = httpx.AsyncClient(base_url=base_url, headers={}, cookies={})
        client_async = client_from_httpx(raw_async, config=ClientConfig(auth=providers))
        async with client_async:
            return await operation.async_api(client_async).protected()  # type: ignore[attr-defined,no-any-return]

    def execute_sync() -> NormalizedResponse[object]:
        raw_sync = httpx.Client(base_url=base_url, headers={}, cookies={})
        client_sync = client_from_httpx(raw_sync, config=ClientConfig(auth=providers))
        with client_sync:
            return operation.sync_api(client_sync).protected()  # type: ignore[attr-defined,no-any-return]

    return await asyncio.to_thread(execute_sync)


@pytest.mark.parametrize("async_client", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("case", STATIC_CASES, ids=lambda case: case.name)
async def test_static_auth_round_trips_through_a_real_localhost_socket(
    httpserver: HTTPServer,
    case: StaticAuthCase,
    async_client: bool,
) -> None:
    httpserver.expect_oneshot_request(
        "/protected",
        method="GET",
        headers=case.expected_headers,
        query_string=case.expected_query,
    ).respond_with_json({"authenticated": True, "scheme": case.name})

    response = await _execute(
        async_client=async_client,
        base_url=httpserver.url_for("/"),
        operation=_raw_operation(case.scheme),
        providers=_providers((case.scheme, case.credential)),
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "scheme": case.name}
    httpserver.check()


@pytest.mark.parametrize("async_client", [False, True], ids=["sync", "async"])
async def test_and_security_applies_every_scheme_atomically(
    httpserver: HTTPServer,
    async_client: bool,
) -> None:
    user = BearerScheme("user")
    device = ApiKeyScheme.header("X-Device-Key", name="device")
    policy = any_of(all_of(user, device))
    httpserver.expect_oneshot_request(
        "/protected",
        headers={
            "Authorization": "Bearer user-token",
            "X-Device-Key": "device-token",
        },
    ).respond_with_json({"alternative": "user-and-device"})

    response = await _execute(
        async_client=async_client,
        base_url=httpserver.url_for("/"),
        operation=_raw_operation(policy, operation_id="compositeAnd"),
        providers=_providers((user, "user-token"), (device, "device-token")),
    )

    assert response.status_code == 200
    assert response.json() == {"alternative": "user-and-device"}
    httpserver.check()


@pytest.mark.parametrize("async_client", [False, True], ids=["sync", "async"])
async def test_or_security_selects_the_first_complete_alternative(
    httpserver: HTTPServer,
    async_client: bool,
) -> None:
    user = BearerScheme("user")
    device = ApiKeyScheme.header("X-Device-Key", name="device")
    fallback = CookieScheme("fallback_session", name="fallback")
    policy = any_of(all_of(user, device), fallback)
    httpserver.expect_oneshot_request(
        "/protected",
        headers={"Cookie": "fallback_session=fallback-token"},
    ).respond_with_json({"alternative": "fallback"})

    response = await _execute(
        async_client=async_client,
        base_url=httpserver.url_for("/"),
        operation=_raw_operation(policy, operation_id="compositeOr"),
        providers=_providers((fallback, "fallback-token")),
    )

    assert response.status_code == 200
    assert response.json() == {"alternative": "fallback"}
    httpserver.check()


@pytest.mark.parametrize("async_client", [False, True], ids=["sync", "async"])
async def test_static_bearer_challenge_is_returned_without_an_implicit_refresh(
    httpserver: HTTPServer,
    async_client: bool,
) -> None:
    scheme = BearerScheme()
    httpserver.expect_oneshot_request(
        "/protected",
        headers={"Authorization": "Bearer revoked-token"},
    ).respond_with_json(
        {"error": "invalid_token"},
        status=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )

    response = await _execute(
        async_client=async_client,
        base_url=httpserver.url_for("/"),
        operation=_raw_operation(scheme, operation_id="staticBearerChallenge"),
        providers=_providers((scheme, "revoked-token")),
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}
    assert cast_headers(response.headers)["www-authenticate"] == 'Bearer error="invalid_token"'
    httpserver.check()
