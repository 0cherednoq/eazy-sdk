from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast

import httpx
import pytest
from curl_cffi import requests as curl_requests
from pytest_httpserver import HTTPServer, RequestMatcher

from eazy_sdk import AsyncApi, AsyncClient, ClientConfig, api
from eazy_sdk.auth import (
    Auth,
    AuthScheme,
)
from eazy_sdk.auth.core import AttributeSessionSelector
from eazy_sdk.clients import CallOptions, UnsafeReplayError
from eazy_sdk.ext import (
    AuthFlowContext,
    AuthLocation,
    AuthPlacement,
    AuthProviders,
    MemorySessionStore,
    SessionAuth,
    SessionKey,
    SessionProvider,
    SessionRevision,
    StoredSession,
)
from eazy_sdk.response import NormalizedResponse, ResponseContext, Responses
from tests._support.zapros_clients import client_from_curl_cffi, client_from_httpx

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class LoginCredentials:
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class BearerSession:
    access_token: str
    refresh_token: str
    valid: bool = True


@dataclass(frozen=True, slots=True)
class CookieSession:
    session_id: str
    valid: bool = True


@dataclass(frozen=True, slots=True)
class BearerSessionValidator:
    def __call__(self, value: object) -> BearerSession:
        if not isinstance(value, BearerSession) or not value.access_token:
            raise TypeError("invalid bearer session")
        return value


@dataclass(frozen=True, slots=True)
class CookieSessionValidator:
    def __call__(self, value: object) -> CookieSession:
        if not isinstance(value, CookieSession) or not value.session_id:
            raise TypeError("invalid cookie session")
        return value


def _bearer_scheme() -> AuthScheme[BearerSession]:
    return AuthScheme(
        "user-session",
        BearerSessionValidator(),
        (
            AuthPlacement(
                AuthLocation.HEADER,
                "Authorization",
                AttributeSessionSelector("access_token"),
                "Bearer ",
            ),
        ),
    )


def _cookie_scheme() -> AuthScheme[CookieSession]:
    return AuthScheme(
        "cookie-session",
        CookieSessionValidator(),
        (
            AuthPlacement(
                AuthLocation.COOKIE,
                "session_id",
                AttributeSessionSelector("session_id"),
            ),
        ),
    )


def _query_session_scheme() -> AuthScheme[BearerSession]:
    return AuthScheme(
        "query-session",
        BearerSessionValidator(),
        (
            AuthPlacement(
                AuthLocation.QUERY,
                "session_token",
                AttributeSessionSelector("access_token"),
            ),
        ),
    )


async def _protected_call[T](
    client: AsyncClient,
    security: AuthScheme[T],
    *,
    method: str = "GET",
    path: str = "/protected",
    options: CallOptions | None = None,
) -> NormalizedResponse[object]:
    decorator = api.get if method == "GET" else api.post

    class ProtectedApi(AsyncApi):
        @decorator(
            path,
            operation_id="sessionProtectedResource",
            responses=Responses(success=()),
            security=security,
            raw_response=True,
        )
        async def protected(
            self, *, options: CallOptions | None = None
        ) -> NormalizedResponse[object]:
            raise NotImplementedError

    return await ProtectedApi(client).protected(options=options)


class ScopedAuthSdk:
    def __init__(self, client: AsyncClient, origin: str = "") -> None:
        self._client = client
        self._origin = origin.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self._origin}{path}" if self._origin else path

    async def login_bearer(self, credentials: LoginCredentials) -> BearerSession:
        response = await self._client.post(
            self._url("/session/login"),
            json={"username": credentials.username, "password": credentials.password},
        )
        if response.status_code != 200:
            raise RuntimeError(f"login failed with {response.status_code}")
        value = response.json()
        if not isinstance(value, dict):
            raise TypeError("login response must be an object")
        access_token = value.get("access_token")
        refresh_token = value.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise TypeError("login response is missing tokens")
        return BearerSession(access_token, refresh_token)

    async def login_cookie(self, credentials: LoginCredentials) -> CookieSession:
        response = await self._client.post(
            self._url("/session/login"),
            json={"username": credentials.username, "password": credentials.password},
        )
        if response.status_code != 200:
            raise RuntimeError(f"login failed with {response.status_code}")
        cookies = ResponseContext(cast(NormalizedResponse[object], response)).cookies.values
        session_ids = [value for name, value in cookies if name == "session_id"]
        if len(session_ids) != 1:
            raise TypeError("login response must set exactly one session_id cookie")
        return CookieSession(session_ids[0])

    async def login_bearer_from_headers(self, credentials: LoginCredentials) -> BearerSession:
        response = await self._client.post(
            self._url("/session/login-header"),
            json={"username": credentials.username, "password": credentials.password},
        )
        if response.status_code != 200:
            raise RuntimeError(f"login failed with {response.status_code}")
        headers = ResponseContext(cast(NormalizedResponse[object], response)).headers
        access_token = headers.get("x-access-token")
        refresh_token = headers.get("x-refresh-token")
        if not access_token or not refresh_token:
            raise TypeError("login response is missing token headers")
        return BearerSession(access_token, refresh_token)

    async def refresh_bearer(self, session: BearerSession) -> BearerSession:
        response = await self._client.post(
            self._url("/session/refresh"),
            json={"refresh_token": session.refresh_token},
        )
        if response.status_code != 200:
            raise RuntimeError(f"refresh failed with {response.status_code}")
        value = response.json()
        if not isinstance(value, dict):
            raise TypeError("refresh response must be an object")
        access_token = value.get("access_token")
        refresh_token = value.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise TypeError("refresh response is missing tokens")
        return BearerSession(access_token, refresh_token)


@dataclass
class BearerAcquirer:
    calls: int = 0

    async def acquire(
        self,
        credentials: LoginCredentials,
        context: AuthFlowContext[ScopedAuthSdk],
    ) -> BearerSession:
        self.calls += 1
        return await context.sdk.login_bearer(credentials)


@dataclass
class CookieAcquirer:
    calls: int = 0

    async def acquire(
        self,
        credentials: LoginCredentials,
        context: AuthFlowContext[ScopedAuthSdk],
    ) -> CookieSession:
        self.calls += 1
        return await context.sdk.login_cookie(credentials)


@dataclass
class HeaderAcquirer:
    calls: int = 0

    async def acquire(
        self,
        credentials: LoginCredentials,
        context: AuthFlowContext[ScopedAuthSdk],
    ) -> BearerSession:
        self.calls += 1
        return await context.sdk.login_bearer_from_headers(credentials)


@dataclass
class BearerRefresher:
    calls: int = 0

    async def refresh(
        self,
        session: BearerSession,
        context: AuthFlowContext[ScopedAuthSdk],
    ) -> BearerSession:
        self.calls += 1
        return await context.sdk.refresh_bearer(session)


async def _client_with_stored_bearer(
    httpserver: HTTPServer,
    *,
    async_adapter: str = "httpx-async",
) -> tuple[
    AsyncClient,
    MemorySessionStore[BearerSession],
    SessionKey,
    AuthScheme[BearerSession],
    BearerRefresher,
]:
    client_box: list[AsyncClient] = []
    scheme = _bearer_scheme()
    store: MemorySessionStore[BearerSession] = MemorySessionStore()
    key = SessionKey("account:museum-user")
    await store.save(key, BearerSession("access-v1", "refresh-v1"), SessionRevision(1))
    refresher = BearerRefresher()
    provider: SessionProvider[LoginCredentials, BearerSession, ScopedAuthSdk] = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=key,
            sdk_factory=lambda _graph: ScopedAuthSdk(
                client_box[0], "" if async_adapter == "httpx-async" else httpserver.url_for("")
            ),
            store=store,
            validate=lambda session: session.valid,
            refresh=refresher,
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)
    client = _async_client(async_adapter, httpserver, scheme, providers)
    client_box.append(client)
    return client, store, key, scheme, refresher


def _async_client(
    adapter: str,
    httpserver: HTTPServer,
    scheme: AuthScheme[Any],
    providers: AuthProviders,
) -> AsyncClient:
    if adapter == "httpx-async":
        raw = httpx.AsyncClient(base_url=httpserver.url_for("/"), headers={}, cookies={})
        return client_from_httpx(raw, config=ClientConfig(auth=Auth._bind(scheme, providers)))
    raw_curl = curl_requests.AsyncSession()
    return client_from_curl_cffi(
        raw_curl,
        base_url=httpserver.url_for("/"),
        config=ClientConfig(auth=Auth._bind(scheme, providers)),
    )


def _adapter_url(adapter: str, httpserver: HTTPServer, path: str) -> str:
    return path if adapter == "httpx-async" else httpserver.url_for(path)


@pytest.mark.parametrize(
    "paths",
    [
        ("/protected",) * 12,
        tuple(f"/protected/{index % 3}" for index in range(12)),
    ],
    ids=["same-operation", "different-operations"],
)
@pytest.mark.parametrize("async_adapter", ["httpx-async", "curl-cffi-async"])
async def test_concurrent_credentials_login_uses_the_same_client_and_singleflight(
    httpserver: HTTPServer,
    paths: tuple[str, ...],
    async_adapter: str,
) -> None:
    login_matcher = RequestMatcher(
        "/session/login",
        method="POST",
        json={"username": "museum-user", "password": "secret"},
    )
    httpserver.expect_oneshot_request(
        "/session/login",
        method="POST",
        json={"username": "museum-user", "password": "secret"},
    ).respond_with_json({"access_token": "access-v1", "refresh_token": "refresh-v1"})
    protected_matchers = tuple(
        RequestMatcher(
            path,
            method="GET",
            headers={"Authorization": "Bearer access-v1"},
        )
        for path in sorted(set(paths))
    )
    for matcher in protected_matchers:
        httpserver.expect_request(
            matcher.uri,
            method="GET",
            headers={"Authorization": "Bearer access-v1"},
        ).respond_with_json({"authenticated": True})

    client_box: list[AsyncClient] = []
    scheme = _bearer_scheme()
    store: MemorySessionStore[BearerSession] = MemorySessionStore()
    acquirer = BearerAcquirer()
    provider = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=SessionKey("account:museum-user"),
            sdk_factory=lambda _graph: ScopedAuthSdk(
                client_box[0], "" if async_adapter == "httpx-async" else httpserver.url_for("")
            ),
            store=store,
            validate=lambda session: session.valid,
            credentials=LoginCredentials("museum-user", "secret"),
            acquire=acquirer,
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)
    client = _async_client(async_adapter, httpserver, scheme, providers)
    client_box.append(client)

    async with client:
        responses = await asyncio.gather(
            *(
                _protected_call(
                    client,
                    scheme,
                    path=_adapter_url(async_adapter, httpserver, path),
                )
                for path in paths
            )
        )

    assert [response.status_code for response in responses] == [200] * len(paths)
    assert [response.json() for response in responses] == [{"authenticated": True}] * len(paths)
    assert acquirer.calls == 1
    assert httpserver.get_matching_requests_count(login_matcher) == 1
    assert sum(
        httpserver.get_matching_requests_count(matcher) for matcher in protected_matchers
    ) == len(paths)
    assert await store.load(SessionKey("account:museum-user")) == StoredSession(
        BearerSession("access-v1", "refresh-v1"), SessionRevision(1)
    )
    httpserver.check()


async def test_cookie_login_extracts_multiple_set_cookie_lines_and_applies_session_cookie(
    httpserver: HTTPServer,
) -> None:
    httpserver.expect_oneshot_request(
        "/session/login",
        method="POST",
        json={"username": "museum-user", "password": "secret"},
    ).respond_with_data(
        '{"authenticated":true}',
        content_type="application/json",
        headers=(
            ("Set-Cookie", "csrf=csrf-v1; Path=/; HttpOnly"),
            ("Set-Cookie", "session_id=session-v1; Path=/; HttpOnly"),
        ),
    )
    httpserver.expect_oneshot_request(
        "/protected",
        headers={"Cookie": "session_id=session-v1"},
    ).respond_with_json({"authenticated": True, "via": "cookie"})

    raw = httpx.AsyncClient(base_url=httpserver.url_for("/"), headers={}, cookies={})
    client_box: list[AsyncClient] = []
    scheme = _cookie_scheme()
    store: MemorySessionStore[CookieSession] = MemorySessionStore()
    acquirer = CookieAcquirer()
    provider = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=SessionKey("cookie:museum-user"),
            sdk_factory=lambda _graph: ScopedAuthSdk(client_box[0]),
            store=store,
            validate=lambda session: session.valid,
            credentials=LoginCredentials("museum-user", "secret"),
            acquire=acquirer,
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)
    client = client_from_httpx(raw, config=ClientConfig(auth=Auth._bind(scheme, providers)))
    client_box.append(client)

    async with client:
        response = await _protected_call(client, scheme)

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "via": "cookie"}
    assert acquirer.calls == 1
    assert await store.load(SessionKey("cookie:museum-user")) == StoredSession(
        CookieSession("session-v1"), SessionRevision(1)
    )
    httpserver.check()


async def test_login_extracts_session_from_response_headers_and_applies_it_to_query(
    httpserver: HTTPServer,
) -> None:
    httpserver.expect_oneshot_request(
        "/session/login-header",
        method="POST",
        json={"username": "museum-user", "password": "secret"},
    ).respond_with_json(
        {"authenticated": True},
        headers={
            "X-Access-Token": "access-from-header",
            "X-Refresh-Token": "refresh-from-header",
        },
    )
    httpserver.expect_oneshot_request(
        "/protected",
        query_string={"session_token": "access-from-header"},
    ).respond_with_json({"authenticated": True, "via": "query"})

    raw = httpx.AsyncClient(base_url=httpserver.url_for("/"), headers={}, cookies={})
    client_box: list[AsyncClient] = []
    scheme = _query_session_scheme()
    store: MemorySessionStore[BearerSession] = MemorySessionStore()
    acquirer = HeaderAcquirer()
    provider: SessionProvider[LoginCredentials, BearerSession, ScopedAuthSdk] = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=SessionKey("header:museum-user"),
            sdk_factory=lambda _graph: ScopedAuthSdk(client_box[0]),
            store=store,
            validate=lambda session: session.valid,
            credentials=LoginCredentials("museum-user", "secret"),
            acquire=acquirer,
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)
    client = client_from_httpx(raw, config=ClientConfig(auth=Auth._bind(scheme, providers)))
    client_box.append(client)

    async with client:
        response = await _protected_call(client, scheme)

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "via": "query"}
    assert acquirer.calls == 1
    assert await store.load(SessionKey("header:museum-user")) == StoredSession(
        BearerSession("access-from-header", "refresh-from-header"), SessionRevision(1)
    )
    httpserver.check()


async def test_passed_valid_session_skips_login_and_is_applied_to_the_request(
    httpserver: HTTPServer,
) -> None:
    login_matcher = RequestMatcher("/session/login", method="POST")
    protected_matcher = RequestMatcher(
        "/protected",
        headers={"Authorization": "Bearer provided-access"},
    )
    httpserver.expect_request(
        "/protected",
        headers={"Authorization": "Bearer provided-access"},
    ).respond_with_json({"authenticated": True, "source": "provided-session"})

    raw = httpx.AsyncClient(base_url=httpserver.url_for("/"), headers={}, cookies={})
    client_box: list[AsyncClient] = []
    scheme = _bearer_scheme()
    store: MemorySessionStore[BearerSession] = MemorySessionStore()
    key = SessionKey("provided:museum-user")
    acquirer = BearerAcquirer()
    provider: SessionProvider[LoginCredentials, BearerSession, ScopedAuthSdk] = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=key,
            sdk_factory=lambda _graph: ScopedAuthSdk(client_box[0]),
            store=store,
            validate=lambda session: session.valid,
            initial_session=BearerSession("provided-access", "provided-refresh"),
            acquire=acquirer,
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)
    client = client_from_httpx(raw, config=ClientConfig(auth=Auth._bind(scheme, providers)))
    client_box.append(client)

    async with client:
        first = await _protected_call(client, scheme)
        second = await _protected_call(client, scheme)

    assert (
        first.json()
        == second.json()
        == {
            "authenticated": True,
            "source": "provided-session",
        }
    )
    assert acquirer.calls == 0
    assert httpserver.get_matching_requests_count(login_matcher) == 0
    assert httpserver.get_matching_requests_count(protected_matcher) == 2
    assert await store.load(key) == StoredSession(
        BearerSession("provided-access", "provided-refresh"), SessionRevision(1)
    )
    httpserver.check()


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_login_http_failure_does_not_call_resource_or_replace_stored_session(
    httpserver: HTTPServer,
    status: int,
) -> None:
    httpserver.expect_oneshot_request("/session/login", method="POST").respond_with_json(
        {"error": "login_failed"}, status=status
    )
    protected_matcher = RequestMatcher("/protected")
    raw = httpx.AsyncClient(base_url=httpserver.url_for("/"), headers={}, cookies={})
    client_box: list[AsyncClient] = []
    scheme = _bearer_scheme()
    store: MemorySessionStore[BearerSession] = MemorySessionStore()
    key = SessionKey(f"failed-login:{status}")
    expired = BearerSession("expired-access", "refresh-v0", valid=False)
    await store.save(key, expired, SessionRevision(4))
    acquirer = BearerAcquirer()
    provider: SessionProvider[LoginCredentials, BearerSession, ScopedAuthSdk] = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=key,
            sdk_factory=lambda _graph: ScopedAuthSdk(client_box[0]),
            store=store,
            validate=lambda session: session.valid,
            credentials=LoginCredentials("museum-user", "secret"),
            acquire=acquirer,
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)
    client = client_from_httpx(raw, config=ClientConfig(auth=Auth._bind(scheme, providers)))
    client_box.append(client)

    async with client:
        with pytest.raises(RuntimeError, match=f"login failed with {status}"):
            await _protected_call(client, scheme)

    assert acquirer.calls == 1
    assert httpserver.get_matching_requests_count(protected_matcher) == 0
    assert await store.load(key) == StoredSession(expired, SessionRevision(4))
    httpserver.check()


async def test_malformed_login_response_does_not_replace_an_expired_stored_session(
    httpserver: HTTPServer,
) -> None:
    httpserver.expect_oneshot_request(
        "/session/login",
        method="POST",
    ).respond_with_json({"refresh_token": "refresh-without-access"})

    raw = httpx.AsyncClient(base_url=httpserver.url_for("/"), headers={}, cookies={})
    client_box: list[AsyncClient] = []
    scheme = _bearer_scheme()
    store: MemorySessionStore[BearerSession] = MemorySessionStore()
    key = SessionKey("account:museum-user")
    expired = BearerSession("expired-access", "refresh-v0", valid=False)
    await store.save(key, expired, SessionRevision(4))
    provider = SessionProvider(
        SessionAuth(
            scheme=scheme,
            key=key,
            sdk_factory=lambda _graph: ScopedAuthSdk(client_box[0]),
            store=store,
            validate=lambda session: session.valid,
            credentials=LoginCredentials("museum-user", "secret"),
            acquire=BearerAcquirer(),
        )
    )
    providers = AuthProviders()
    providers.register(scheme, provider)
    client = client_from_httpx(raw, config=ClientConfig(auth=Auth._bind(scheme, providers)))
    client_box.append(client)

    async with client:
        with pytest.raises(TypeError, match="missing tokens"):
            await _protected_call(client, scheme)

    assert await store.load(key) == StoredSession(expired, SessionRevision(4))
    httpserver.check()


async def test_bearer_401_refreshes_selected_session_and_replays_a_fresh_attempt(
    httpserver: HTTPServer,
) -> None:
    old_resource = RequestMatcher(
        "/protected",
        headers={"Authorization": "Bearer access-v1"},
    )
    refresh_request = RequestMatcher(
        "/session/refresh",
        method="POST",
        json={"refresh_token": "refresh-v1"},
    )
    new_resource = RequestMatcher(
        "/protected",
        headers={"Authorization": "Bearer access-v2"},
    )
    httpserver.expect_oneshot_request(
        "/protected",
        headers={"Authorization": "Bearer access-v1"},
    ).respond_with_json(
        {"error": "invalid_token"},
        status=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )
    httpserver.expect_oneshot_request(
        "/session/refresh",
        method="POST",
        json={"refresh_token": "refresh-v1"},
    ).respond_with_json({"access_token": "access-v2", "refresh_token": "refresh-v2"})
    httpserver.expect_oneshot_request(
        "/protected",
        headers={"Authorization": "Bearer access-v2"},
    ).respond_with_json({"authenticated": True, "revision": 2})

    client, store, key, scheme, refresher = await _client_with_stored_bearer(httpserver)

    async with client:
        response = await _protected_call(
            client,
            scheme,
            options=CallOptions(max_attempts=2, auth_retries=1),
        )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "revision": 2}
    assert refresher.calls == 1
    assert httpserver.get_matching_requests_count(old_resource) == 1
    assert httpserver.get_matching_requests_count(refresh_request) == 1
    assert httpserver.get_matching_requests_count(new_resource) == 1
    assert await store.load(key) == StoredSession(
        BearerSession("access-v2", "refresh-v2"), SessionRevision(2)
    )
    httpserver.check()


@pytest.mark.parametrize("async_adapter", ["httpx-async", "curl-cffi-async"])
async def test_concurrent_401_responses_trigger_one_refresh_for_all_logical_calls(
    httpserver: HTTPServer,
    async_adapter: str,
) -> None:
    paths = tuple(f"/protected/{index % 3}" for index in range(12))
    old_matchers = tuple(
        RequestMatcher(path, headers={"Authorization": "Bearer access-v1"})
        for path in sorted(set(paths))
    )
    new_matchers = tuple(
        RequestMatcher(path, headers={"Authorization": "Bearer access-v2"})
        for path in sorted(set(paths))
    )
    refresh_matcher = RequestMatcher(
        "/session/refresh",
        method="POST",
        json={"refresh_token": "refresh-v1"},
    )
    for matcher in old_matchers:
        httpserver.expect_request(
            matcher.uri,
            headers={"Authorization": "Bearer access-v1"},
        ).respond_with_json(
            {"error": "invalid_token"},
            status=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    for matcher in new_matchers:
        httpserver.expect_request(
            matcher.uri,
            headers={"Authorization": "Bearer access-v2"},
        ).respond_with_json({"authenticated": True, "revision": 2})
    httpserver.expect_oneshot_request(
        "/session/refresh",
        method="POST",
        json={"refresh_token": "refresh-v1"},
    ).respond_with_json({"access_token": "access-v2", "refresh_token": "refresh-v2"})
    client, store, key, scheme, refresher = await _client_with_stored_bearer(
        httpserver,
        async_adapter=async_adapter,
    )

    async with client:
        responses = await asyncio.gather(
            *(
                _protected_call(
                    client,
                    scheme,
                    path=_adapter_url(async_adapter, httpserver, path),
                    options=CallOptions(max_attempts=2, auth_retries=1),
                )
                for path in paths
            )
        )

    assert [response.status_code for response in responses] == [200] * len(paths)
    assert [response.json() for response in responses] == [
        {"authenticated": True, "revision": 2}
    ] * len(paths)
    assert refresher.calls == 1
    assert httpserver.get_matching_requests_count(refresh_matcher) == 1
    assert sum(httpserver.get_matching_requests_count(matcher) for matcher in old_matchers) >= 1
    assert sum(httpserver.get_matching_requests_count(matcher) for matcher in new_matchers) == len(
        paths
    )
    assert await store.load(key) == StoredSession(
        BearerSession("access-v2", "refresh-v2"), SessionRevision(2)
    )
    httpserver.check()


async def test_repeated_401_exhausts_auth_budget_without_a_second_refresh(
    httpserver: HTTPServer,
) -> None:
    httpserver.expect_oneshot_request(
        "/protected", headers={"Authorization": "Bearer access-v1"}
    ).respond_with_json({"error": "expired"}, status=401)
    httpserver.expect_oneshot_request("/session/refresh", method="POST").respond_with_json(
        {"access_token": "access-v2", "refresh_token": "refresh-v2"}
    )
    httpserver.expect_oneshot_request(
        "/protected", headers={"Authorization": "Bearer access-v2"}
    ).respond_with_json({"error": "revoked"}, status=401)
    client, store, key, scheme, refresher = await _client_with_stored_bearer(httpserver)

    async with client:
        response = await _protected_call(
            client,
            scheme,
            options=CallOptions(max_attempts=3, auth_retries=1),
        )

    assert response.status_code == 401
    assert response.json() == {"error": "revoked"}
    assert refresher.calls == 1
    assert await store.load(key) == StoredSession(
        BearerSession("access-v2", "refresh-v2"), SessionRevision(2)
    )
    httpserver.check()


@pytest.mark.parametrize("status", [403, 500])
async def test_non_auth_failures_do_not_refresh_or_replay(
    httpserver: HTTPServer,
    status: int,
) -> None:
    httpserver.expect_oneshot_request(
        "/protected", headers={"Authorization": "Bearer access-v1"}
    ).respond_with_json({"error": "terminal"}, status=status)
    client, store, key, scheme, refresher = await _client_with_stored_bearer(httpserver)

    async with client:
        response = await _protected_call(
            client,
            scheme,
            options=CallOptions(max_attempts=2, auth_retries=1),
        )

    assert response.status_code == status
    assert refresher.calls == 0
    assert await store.load(key) == StoredSession(
        BearerSession("access-v1", "refresh-v1"), SessionRevision(1)
    )
    httpserver.check()


async def test_refresh_failure_preserves_the_selected_session(
    httpserver: HTTPServer,
) -> None:
    httpserver.expect_oneshot_request(
        "/protected", headers={"Authorization": "Bearer access-v1"}
    ).respond_with_json({"error": "expired"}, status=401)
    httpserver.expect_oneshot_request("/session/refresh", method="POST").respond_with_json(
        {"error": "invalid_grant"}, status=400
    )
    client, store, key, scheme, refresher = await _client_with_stored_bearer(httpserver)

    async with client:
        with pytest.raises(RuntimeError, match="refresh failed with 400"):
            await _protected_call(
                client,
                scheme,
                options=CallOptions(max_attempts=2, auth_retries=1),
            )

    assert refresher.calls == 1
    assert await store.load(key) == StoredSession(
        BearerSession("access-v1", "refresh-v1"), SessionRevision(1)
    )
    httpserver.check()


async def test_unsafe_post_is_not_refreshed_or_replayed_after_401(
    httpserver: HTTPServer,
) -> None:
    httpserver.expect_oneshot_request(
        "/protected",
        method="POST",
        headers={"Authorization": "Bearer access-v1"},
    ).respond_with_json({"error": "expired"}, status=401)
    client, store, key, scheme, refresher = await _client_with_stored_bearer(httpserver)

    async with client:
        with pytest.raises(UnsafeReplayError, match="idempotent"):
            await _protected_call(
                client,
                scheme,
                method="POST",
                options=CallOptions(max_attempts=2, auth_retries=1),
            )

    assert refresher.calls == 0
    assert await store.load(key) == StoredSession(
        BearerSession("access-v1", "refresh-v1"), SessionRevision(1)
    )
    httpserver.check()
