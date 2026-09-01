from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import pytest

from eazy_sdk import ClientConfig, PlanError, SyncApi, api
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
from eazy_sdk.auth.core import AuthProviderIdentity, AuthProviders, StaticAuthProvider
from eazy_sdk.clients import CallOptions
from eazy_sdk.request import Header
from eazy_sdk.response import NormalizedResponse, Responses
from tests._support.zapros_clients import client_from_httpx

pytestmark = pytest.mark.unit


def test_auth_retry_budget_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="retry budgets must not be negative"):
        CallOptions(auth_retries=-1)


def test_auth_secrets_are_absent_from_provider_and_observer_representations() -> None:
    bearer_secret = "bearer-secret-that-must-not-leak"
    query_secret = "query-secret-that-must-not-leak"
    bearer = BearerScheme()
    query = ApiKeyScheme.query("api_key")
    providers = AuthProviders()
    bearer_provider = StaticAuthProvider(bearer, bearer_secret, AuthProviderIdentity("bearer"))
    providers.register(bearer, bearer_provider)
    providers.register(
        query,
        StaticAuthProvider(query, query_secret, AuthProviderIdentity("query")),
    )
    observed: list[object | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    raw = httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    with client_from_httpx(
        raw,
        config=ClientConfig(
            auth=Auth._bind(bearer, providers),
            observer=lambda _phase, value: observed.append(value),
        ),
    ) as client:
        _call(client, any_of(all_of(bearer, query)), operation_id="auth-redaction")

    diagnostic_text = repr((bearer_provider, observed))
    assert bearer_secret not in diagnostic_text
    assert query_secret not in diagnostic_text


@dataclass(frozen=True, slots=True)
class AuthCase:
    scheme: AuthScheme[Any]
    credentials: Any
    expected_target: str = "/auth"
    expected_header: tuple[str, str] | None = None


@pytest.mark.parametrize(
    "case",
    [
        AuthCase(
            BearerScheme(),
            "token",
            expected_header=("authorization", "Bearer token"),
        ),
        AuthCase(
            ApiKeyScheme.header("X-Api-Key"),
            "secret",
            expected_header=("x-api-key", "secret"),
        ),
        AuthCase(ApiKeyScheme.query("api_key"), "secret", expected_target="/auth?api_key=secret"),
        AuthCase(
            CookieScheme("session"),
            "cookie-token",
            expected_header=("cookie", "session=cookie-token"),
        ),
        AuthCase(
            BasicScheme(),
            ("user", "password"),
            expected_header=(
                "authorization",
                "Basic " + base64.b64encode(b"user:password").decode("ascii"),
            ),
        ),
    ],
    ids=["bearer-header", "api-key-header", "api-key-query", "cookie", "basic"],
)
def test_static_auth_is_applied_at_the_declared_wire_destination(case: AuthCase) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, content=b"ok", request=request)

    providers = AuthProviders()
    providers.register(
        case.scheme,
        StaticAuthProvider(case.scheme, case.credentials, AuthProviderIdentity("test")),
    )
    raw = httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    with client_from_httpx(
        raw, config=ClientConfig(auth=Auth._bind(case.scheme, providers))
    ) as client:
        response = _call(client, case.scheme, operation_id="auth")
    assert response.status_code == 200
    assert len(captured) == 1
    assert captured[0].url.raw_path.decode("ascii") == case.expected_target
    if case.expected_header is not None:
        name, value = case.expected_header
        assert captured[0].headers[name] == value


def test_auth_overwrites_a_user_value_in_the_same_declared_slot() -> None:
    captured: list[httpx.Request] = []
    scheme = BearerScheme()
    providers = AuthProviders()
    providers.register(
        scheme,
        StaticAuthProvider(scheme, "auth-token", AuthProviderIdentity("test")),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, request=request)

    raw = httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    with client_from_httpx(raw, config=ClientConfig(auth=Auth._bind(scheme, providers))) as client:
        _call(
            client,
            scheme,
            operation_id="auth-overwrite",
            authorization="Bearer user-value",
        )
    assert captured[0].headers["authorization"] == "Bearer auth-token"


def test_missing_auth_provider_fails_before_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request)

    scheme = BearerScheme()
    raw = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler))
    with (
        client_from_httpx(raw) as client,
        pytest.raises(PlanError, match="no complete security alternative"),
    ):
        _call(client, scheme, operation_id="auth-missing")
    assert calls == 0


@pytest.mark.parametrize(
    ("scheme", "credentials"),
    [
        (BearerScheme(), ""),
        (ApiKeyScheme.header("X-Api-Key"), ""),
        (CookieScheme("session"), ""),
        (BasicScheme(), ("user",)),
    ],
)
def test_invalid_static_credentials_fail_before_transport(
    scheme: AuthScheme[Any], credentials: Any
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request)

    providers = AuthProviders()
    providers.register(
        scheme,
        StaticAuthProvider(scheme, credentials, AuthProviderIdentity("invalid")),
    )
    raw = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler))
    with (
        client_from_httpx(raw, config=ClientConfig(auth=Auth._bind(scheme, providers))) as client,
        pytest.raises(TypeError, match=r"credential|non-empty|two strings"),
    ):
        _call(client, scheme, operation_id="auth-invalid")
    assert calls == 0


def _call(
    client: Any,
    security: object,
    *,
    operation_id: str,
    authorization: str | None = None,
) -> NormalizedResponse[object]:
    class AuthApi(SyncApi):
        @api.get(
            "/auth",
            operation_id=operation_id,
            responses=Responses(success=()),
            security=security,
            raw_response=True,
        )
        def auth(
            self,
            *,
            authorization: Annotated[str | None, Header("Authorization")] = None,
        ) -> NormalizedResponse[object]:
            raise NotImplementedError

    auth_api = AuthApi(client)
    return (
        auth_api.auth()
        if authorization is None
        else auth_api.auth(authorization=authorization)
    )
