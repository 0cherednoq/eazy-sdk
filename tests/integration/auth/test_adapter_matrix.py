from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import pytest
from pytest_httpserver import HTTPServer

from eazy_sdk import AsyncApi, SyncApi, api
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
from eazy_sdk.clients import CallOptions
from eazy_sdk.ext import (
    AuthFlowContext,
    AuthProviderIdentity,
    AuthProviders,
    MemorySessionStore,
    SessionAuth,
    SessionKey,
    SessionProvider,
    SessionRevision,
    StaticAuthProvider,
    StoredSession,
)
from eazy_sdk.response import NormalizedResponse, Responses
from tests._support.client_harness import ClientHarness, HarnessOperation

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class _HarnessOperation:
    async_api: type[AsyncApi]
    sync_api: type[SyncApi]

    async def run_async(
        self, client: Any, options: CallOptions | None
    ) -> NormalizedResponse[object]:
        return await self.async_api(client).call(options=options)  # type: ignore[attr-defined,no-any-return]

    def run_sync(self, client: Any, options: CallOptions | None) -> NormalizedResponse[object]:
        return self.sync_api(client).call(options=options)  # type: ignore[attr-defined,no-any-return]


def _operation(
    url: str,
    security: object,
    *,
    operation_id: str,
) -> HarnessOperation:
    class AsyncEndpoint(AsyncApi):
        @api.get(
            url,
            operation_id=operation_id,
            responses=Responses(success=()),
            security=security,
            raw_response=True,
        )
        async def call(self, *, options: CallOptions | None = None) -> NormalizedResponse[object]:
            raise NotImplementedError

    class SyncEndpoint(SyncApi):
        @api.get(
            url,
            operation_id=operation_id,
            responses=Responses(success=()),
            security=security,
            raw_response=True,
        )
        def call(self, *, options: CallOptions | None = None) -> NormalizedResponse[object]:
            raise NotImplementedError

    return _HarnessOperation(AsyncEndpoint, SyncEndpoint)


@dataclass(frozen=True, slots=True)
class DirectHeaderCase:
    name: str
    scheme: AuthScheme[Any]
    credential: object
    expected: str


DIRECT_HEADER_CASES = (
    DirectHeaderCase(
        "basic",
        BasicScheme(),
        ("museum-user", "secret"),
        "Basic " + base64.b64encode(b"museum-user:secret").decode("ascii"),
    ),
    DirectHeaderCase(
        "api-key-header",
        ApiKeyScheme.header("X-API-Key"),
        "header-secret",
        "header-secret",
    ),
)


@pytest.mark.parametrize("case", DIRECT_HEADER_CASES, ids=lambda case: case.name)
async def test_every_adapter_applies_direct_header_auth_on_the_wire(
    httpserver: HTTPServer,
    client_harness: ClientHarness,
    case: DirectHeaderCase,
) -> None:
    header = "Authorization" if case.name == "basic" else "X-API-Key"
    httpserver.expect_oneshot_request(
        "/adapter-direct-header", headers={header: case.expected}
    ).respond_with_json({"adapter": client_harness.name, "scheme": case.name})
    providers = AuthProviders()
    providers.register(
        case.scheme,
        StaticAuthProvider(
            case.scheme,
            case.credential,
            AuthProviderIdentity(f"{client_harness.name}:{case.name}"),
        ),
    )

    response = await client_harness.execute(
        _operation(
            httpserver.url_for("/adapter-direct-header"),
            case.scheme,
            operation_id=f"adapterDirectHeader:{client_harness.name}:{case.name}",
        ),
        Auth._bind(case.scheme, providers),
    )

    assert response.status_code == 200
    assert response.json() == {"adapter": client_harness.name, "scheme": case.name}
    httpserver.check()


async def test_every_adapter_applies_header_query_and_cookie_auth_on_the_wire(
    httpserver: HTTPServer,
    client_harness: ClientHarness,
) -> None:
    adapter = client_harness.name
    bearer = BearerScheme()
    query = ApiKeyScheme.query("api_key", name="query-key")
    cookie = CookieScheme("session_id")
    policy = any_of(all_of(bearer, query, cookie))
    httpserver.expect_oneshot_request(
        "/adapter-auth",
        headers={
            "Authorization": "Bearer adapter-token",
            "Cookie": "session_id=session-token",
        },
        query_string={"api_key": "query-token"},
    ).respond_with_json({"adapter": adapter, "authenticated": True})
    providers = AuthProviders()
    for index, (scheme, value) in enumerate(
        (
            (bearer, "adapter-token"),
            (query, "query-token"),
            (cookie, "session-token"),
        )
    ):
        providers.register(
            scheme,
            StaticAuthProvider(scheme, value, AuthProviderIdentity(f"{adapter}-{index}")),
        )

    response = await client_harness.execute(
        _operation(
            httpserver.url_for("/adapter-auth"),
            policy,
            operation_id=f"adapterStaticAuth:{adapter}",
        ),
        Auth._bind(bearer, providers),
    )

    assert response.status_code == 200
    assert response.json() == {"adapter": adapter, "authenticated": True}
    httpserver.check()


@dataclass
class RotatingRefresher:
    calls: int = 0

    async def refresh(self, session: str, context: AuthFlowContext[object]) -> str:
        self.calls += 1
        assert session == "access-v1"
        return "access-v2"


@dataclass
class CountingAcquirer:
    calls: int = 0

    async def acquire(self, credentials: str, context: AuthFlowContext[object]) -> str:
        self.calls += 1
        assert credentials == "museum-credentials"
        return "token-from-credentials"


@pytest.mark.parametrize("initial_source", ["credentials", "session"])
async def test_every_adapter_resolves_credentials_or_passed_session_before_transport(
    httpserver: HTTPServer,
    client_harness: ClientHarness,
    initial_source: str,
) -> None:
    expected_token = (
        "token-from-credentials" if initial_source == "credentials" else "token-from-session"
    )
    httpserver.expect_oneshot_request(
        "/adapter-initial-source",
        headers={"Authorization": f"Bearer {expected_token}"},
    ).respond_with_json({"adapter": client_harness.name, "source": initial_source})
    scheme = BearerScheme(f"initial-{initial_source}")
    store: MemorySessionStore[str] = MemorySessionStore()
    acquirer = CountingAcquirer()
    provider: SessionProvider[str, str, object] = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=SessionKey(f"initial:{client_harness.name}:{initial_source}"),
            sdk_factory=lambda _graph: object(),
            store=store,
            validate=lambda value: value.startswith("token-"),
            credentials=("museum-credentials" if initial_source == "credentials" else None),
            initial_session=("token-from-session" if initial_source == "session" else None),
            acquire=acquirer,
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)

    response = await client_harness.execute(
        _operation(
            httpserver.url_for("/adapter-initial-source"),
            scheme,
            operation_id=f"adapterInitial:{client_harness.name}:{initial_source}",
        ),
        Auth._bind(scheme, providers),
    )

    assert response.status_code == 200
    assert response.json() == {
        "adapter": client_harness.name,
        "source": initial_source,
    }
    assert acquirer.calls == (1 if initial_source == "credentials" else 0)
    httpserver.check()


@pytest.mark.parametrize("session_location", ["bearer", "cookie"])
async def test_every_adapter_replays_a_fresh_attempt_after_session_refresh(
    httpserver: HTTPServer,
    client_harness: ClientHarness,
    session_location: str,
) -> None:
    adapter = client_harness.name
    first_headers = (
        {"Authorization": "Bearer access-v1"}
        if session_location == "bearer"
        else {"Cookie": "session_id=access-v1"}
    )
    second_headers = (
        {"Authorization": "Bearer access-v2"}
        if session_location == "bearer"
        else {"Cookie": "session_id=access-v2"}
    )
    httpserver.expect_oneshot_request("/adapter-refresh", headers=first_headers).respond_with_json(
        {"error": "invalid_token"},
        status=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )
    httpserver.expect_oneshot_request("/adapter-refresh", headers=second_headers).respond_with_json(
        {"adapter": adapter, "revision": 2, "location": session_location}
    )
    scheme = (
        BearerScheme("rotating-session")
        if session_location == "bearer"
        else CookieScheme("session_id", name="rotating-session")
    )
    store: MemorySessionStore[str] = MemorySessionStore()
    key = SessionKey(f"adapter:{adapter}")
    await store.save(key, "access-v1", SessionRevision(1))
    refresher = RotatingRefresher()
    provider: SessionProvider[object, str, object] = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=key,
            sdk_factory=lambda _graph: object(),
            store=store,
            validate=lambda value: value.startswith("access-"),
            refresh=refresher,
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)

    response = await client_harness.execute(
        _operation(
            httpserver.url_for("/adapter-refresh"),
            scheme,
            operation_id=f"adapterRefresh:{adapter}",
        ),
        Auth._bind(scheme, providers),
        options=CallOptions(max_attempts=2, auth_retries=1),
    )

    assert response.status_code == 200
    assert response.json() == {
        "adapter": adapter,
        "revision": 2,
        "location": session_location,
    }
    assert refresher.calls == 1
    assert await store.load(key) == StoredSession("access-v2", SessionRevision(2))
    httpserver.check()
