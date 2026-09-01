from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

pytest.importorskip("pytest_iam", reason="install the auth-conformance dependency group")

from eazy_sdk import AsyncApi, SyncApi, api
from eazy_sdk.auth import Auth, BasicScheme, BearerScheme
from eazy_sdk.auth.core import AuthProviderIdentity, AuthProviders, StaticAuthProvider
from eazy_sdk.clients import CallOptions
from eazy_sdk.request import Form
from eazy_sdk.response import NormalizedResponse, Responses
from tests._support.client_harness import ClientHarness

pytestmark = [
    pytest.mark.auth_conformance,
    pytest.mark.timeout(30),
    pytest.mark.filterwarnings(
        "ignore:'token_url' is deprecated:authlib.deprecate.AuthlibDeprecationWarning"
    ),
    pytest.mark.filterwarnings(
        r"ignore:get_jwt_config\(self, grant\) is deprecated:DeprecationWarning"
    ),
]


@dataclass(frozen=True, slots=True)
class _FormOperation:
    url: str
    operation_id: str
    security: object
    values: dict[str, str]

    async def run_async(
        self, client: Any, options: CallOptions | None
    ) -> NormalizedResponse[object]:
        class Api(AsyncApi):
            @api.post(
                self.url,
                operation_id=self.operation_id,
                responses=Responses(success=()),
                security=self.security,
                raw_response=True,
            )
            async def token(
                api_self,
                *,
                grant_type: Annotated[str, Form("grant_type")],
                scope: Annotated[str | None, Form("scope")] = None,
                refresh_token: Annotated[str | None, Form("refresh_token")] = None,
                code: Annotated[str | None, Form("code")] = None,
                redirect_uri: Annotated[str | None, Form("redirect_uri")] = None,
                options: CallOptions | None = None,
            ) -> NormalizedResponse[object]:
                raise NotImplementedError

        return await Api(client).token(**self.values, options=options)

    def run_sync(self, client: Any, options: CallOptions | None) -> NormalizedResponse[object]:
        class Api(SyncApi):
            @api.post(
                self.url,
                operation_id=self.operation_id,
                responses=Responses(success=()),
                security=self.security,
                raw_response=True,
            )
            def token(
                api_self,
                *,
                grant_type: Annotated[str, Form("grant_type")],
                scope: Annotated[str | None, Form("scope")] = None,
                refresh_token: Annotated[str | None, Form("refresh_token")] = None,
                code: Annotated[str | None, Form("code")] = None,
                redirect_uri: Annotated[str | None, Form("redirect_uri")] = None,
                options: CallOptions | None = None,
            ) -> NormalizedResponse[object]:
                raise NotImplementedError

        return Api(client).token(**self.values, options=options)


@dataclass(frozen=True, slots=True)
class _GetOperation:
    url: str
    operation_id: str
    security: object | None = None

    async def run_async(
        self, client: Any, options: CallOptions | None
    ) -> NormalizedResponse[object]:
        class Api(AsyncApi):
            @api.get(
                self.url,
                operation_id=self.operation_id,
                responses=Responses(success=()),
                security=self.security,
                raw_response=True,
            )
            async def value(
                api_self, *, options: CallOptions | None = None
            ) -> NormalizedResponse[object]:
                raise NotImplementedError

        return await Api(client).value(options=options)

    def run_sync(self, client: Any, options: CallOptions | None) -> NormalizedResponse[object]:
        class Api(SyncApi):
            @api.get(
                self.url,
                operation_id=self.operation_id,
                responses=Responses(success=()),
                security=self.security,
                raw_response=True,
            )
            def value(
                api_self, *, options: CallOptions | None = None
            ) -> NormalizedResponse[object]:
                raise NotImplementedError

        return Api(client).value(options=options)


def _oauth_client(iam_server: Any, *, client_id: str) -> Any:
    client = iam_server.models.Client(
        client_id=client_id,
        client_secret="conformance-secret",
        client_name="Eazy SDK conformance client",
        client_uri="http://localhost/eazy_sdk",
        redirect_uris=["http://localhost/eazy_sdk/callback"],
        token_endpoint_auth_method="client_secret_basic",
        grant_types=["authorization_code", "refresh_token", "client_credentials"],
        response_types=["code"],
        scope=["openid", "profile", "email", "museum:read"],
    )
    iam_server.backend.save(client)
    return client


def _basic_providers(
    client_id: str, *, client_secret: str = "conformance-secret"
) -> tuple[Any, AuthProviders]:
    scheme = BasicScheme("oauth-client")
    providers = AuthProviders()
    providers.register(
        scheme,
        StaticAuthProvider(
            scheme,
            (client_id, client_secret),
            AuthProviderIdentity(f"oauth:{client_id}"),
        ),
    )
    return scheme, providers


async def _post_form(
    iam_server: Any,
    client_harness: ClientHarness,
    *,
    operation_id: str,
    values: dict[str, str],
    client_id: str,
    client_secret: str = "conformance-secret",
) -> NormalizedResponse[object]:
    scheme, providers = _basic_providers(client_id, client_secret=client_secret)
    return await client_harness.execute(
        _FormOperation(
            iam_server.url.rstrip("/") + "/oauth/token",
            operation_id,
            scheme,
            values,
        ),
        Auth._bind(scheme, providers),
    )


async def test_oidc_discovery_is_reachable_through_every_eazy_sdk_client(
    iam_server: Any,
    client_harness: ClientHarness,
) -> None:
    response = await client_harness.execute(
        _GetOperation(
            iam_server.url.rstrip("/") + "/.well-known/openid-configuration",
            f"oidcDiscovery:{client_harness.name}",
        )
    )

    document = response.json()
    assert response.status_code == 200
    assert document["issuer"] == iam_server.url.rstrip("/")
    assert document["authorization_endpoint"] == iam_server.url.rstrip("/") + "/oauth/authorize"
    assert document["token_endpoint"] == iam_server.url.rstrip("/") + "/oauth/token"
    assert document["jwks_uri"] == iam_server.url.rstrip("/") + "/oauth/jwks.json"
    assert "authorization_code" in document["grant_types_supported"]
    assert "client_credentials" in document["grant_types_supported"]


async def test_oauth_client_credentials_and_error_response_use_real_iam(
    iam_server: Any,
    client_harness: ClientHarness,
) -> None:
    client_id = f"eazy_sdk-client-credentials-{client_harness.name}"
    _oauth_client(iam_server, client_id=client_id)

    response = await _post_form(
        iam_server,
        client_harness,
        operation_id="oauthClientCredentials",
        values={"grant_type": "client_credentials", "scope": "museum:read"},
        client_id=client_id,
    )
    token = response.json()

    assert response.status_code == 200
    assert token["token_type"] == "Bearer"
    assert isinstance(token["access_token"], str) and token["access_token"]
    assert token["scope"] == "museum:read"
    assert token["expires_in"] > 0

    invalid = await _post_form(
        iam_server,
        client_harness,
        operation_id="oauthInvalidClient",
        values={"grant_type": "client_credentials", "scope": "museum:read"},
        client_id=client_id,
        client_secret="wrong-secret",
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"] == "invalid_client"


async def test_oidc_authorization_code_userinfo_and_refresh_round_trip(
    iam_server: Any,
    client_harness: ClientHarness,
) -> None:
    client_id = f"eazy_sdk-authorization-code-{client_harness.name}"
    oauth_client = _oauth_client(iam_server, client_id=client_id)
    user = iam_server.random_user()
    iam_server.login(user)
    iam_server.consent(user, oauth_client)
    redirect_uri = "http://localhost/eazy_sdk/callback"
    async with httpx.AsyncClient(
        base_url=iam_server.url,
        headers={},
        cookies={},
        follow_redirects=False,
    ) as authorization_client:
        authorization = await authorization_client.get(
            "/oauth/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid profile email",
                "state": "state-v1",
                "nonce": "nonce-v1",
                "prompt": "none",
            },
        )

    assert authorization.status_code == 302
    location = cast(str, authorization.headers.get("location"))
    query = parse_qs(urlsplit(location).query)
    assert query["state"] == ["state-v1"]
    code = query["code"][0]

    exchanged = await _post_form(
        iam_server,
        client_harness,
        operation_id="oidcAuthorizationCode",
        values={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        client_id=client_id,
    )
    token = exchanged.json()
    assert exchanged.status_code == 200
    assert token["token_type"] == "Bearer"
    assert len(token["id_token"].split(".")) == 3
    assert token["refresh_token"]

    bearer = token["access_token"]
    bearer_scheme = BearerScheme("oidc-access-token")
    bearer_providers = AuthProviders()
    bearer_providers.register(
        bearer_scheme,
        StaticAuthProvider(
            bearer_scheme,
            bearer,
            AuthProviderIdentity(f"oidc-userinfo:{client_harness.name}"),
        ),
    )
    userinfo = await client_harness.execute(
        _GetOperation(
            iam_server.url.rstrip("/") + "/oauth/userinfo",
            f"oidcUserInfo:{client_harness.name}",
            bearer_scheme,
        ),
        Auth._bind(bearer_scheme, bearer_providers),
    )
    assert userinfo.status_code == 200
    assert userinfo.json()["sub"] == user.user_name

    refreshed = await _post_form(
        iam_server,
        client_harness,
        operation_id="oauthRefreshToken",
        values={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "scope": "openid profile email",
        },
        client_id=client_id,
    )
    refreshed_token = refreshed.json()
    assert refreshed.status_code == 200
    assert refreshed_token["access_token"] != bearer
    assert refreshed_token["token_type"] == "Bearer"
    assert refreshed_token["expires_in"] > 0
