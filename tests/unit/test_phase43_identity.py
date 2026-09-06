"""Phase 43: identity owns the session; the transport only carries the bytes."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from eazy_sdk import (
    AsyncApi,
    AsyncClient,
    AsyncRoot,
    ClientConfig,
    Identity,
    api,
    api_group,
)
from eazy_sdk.auth import (
    AuthContext,
    Bearer,
    ExpiresAt,
    RefreshToken,
    session_scheme,
)
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request.markers import JsonBody
from eazy_sdk.response import Json, NormalizedResponse, Responses

MAIN = "https://api.shop.com"
PAY = "https://pay.shop.com"


class LoginCredentials(BaseModel):
    username: str
    password: SecretStr


class LoginRequest(BaseModel):
    username: str
    password: str


class UserSession(BaseModel):
    access_token: Annotated[SecretStr, Bearer()]
    refresh_token: Annotated[SecretStr, RefreshToken()]
    expires_at: Annotated[datetime, ExpiresAt(leeway=timedelta(seconds=10))]


SHOP_SESSION = session_scheme(UserSession, name="shop-session")


class AuthApi(AsyncApi):
    @api.post(
        "/session/login",
        operation_id="login",
        responses=Responses(success={200: Json(UserSession)}),
        security=None,
    )
    async def login(self, *, body: Annotated[LoginRequest, JsonBody()]) -> UserSession:
        raise NotImplementedError

    @api.post(
        "/session/refresh",
        operation_id="refresh",
        responses=Responses(success={200: Json(UserSession)}),
        security=None,
    )
    async def refresh(self, *, body: Annotated[LoginRequest, JsonBody()]) -> UserSession:
        raise NotImplementedError


class PaymentsService:
    base_url = PAY
    allow = (SHOP_SESSION,)


class BooksApi(AsyncApi):
    security = SHOP_SESSION

    @api.get("/books", operation_id="books", responses=Responses(success=()), raw_response=True)
    async def get(self) -> NormalizedResponse[object]:
        raise NotImplementedError


class CardApi(PaymentsService, AsyncApi):
    security = SHOP_SESSION

    @api.get("/v1/cards", operation_id="cards", responses=Responses(success=()), raw_response=True)
    async def get(self) -> NormalizedResponse[object]:
        raise NotImplementedError


class ShopSdk(AsyncRoot):
    auth = api_group(AuthApi)
    books = api_group(BooksApi)
    card = api_group(CardApi)


class ShopLogin:
    """Turns credentials into a session by calling the SDK's own login operation."""

    def __init__(self) -> None:
        self.logins = 0
        self.refreshes = 0

    async def acquire(
        self, credentials: LoginCredentials, context: AuthContext[Any]
    ) -> UserSession:
        self.logins += 1
        session = await context.sdk.auth.login(
            body=LoginRequest(
                username=credentials.username,
                password=credentials.password.get_secret_value(),
            )
        )
        return cast(UserSession, session)

    async def refresh(self, session: UserSession, context: AuthContext[Any]) -> UserSession:
        self.refreshes += 1
        refreshed = await context.sdk.auth.refresh(
            body=LoginRequest(username="ada", password="refresh")
        )
        return cast(UserSession, refreshed)


class Server:
    """One handler for both hosts, so the test proves a shared session, not shared hosts."""

    def __init__(self, *, expire_first: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.issued = 0
        self.expire_first = expire_first

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((f"{request.url.scheme}://{request.url.host}{path}",
                           request.headers.get("Authorization", "")))
        if path in {"/session/login", "/session/refresh"}:
            self.issued += 1
            expires = datetime(2030, 1, 1, tzinfo=UTC) + timedelta(hours=1)
            return httpx.Response(
                200,
                json={
                    "access_token": f"access-{self.issued}",
                    "refresh_token": f"refresh-{self.issued}",
                    "expires_at": expires.isoformat(),
                },
            )
        if self.expire_first and request.headers.get("Authorization") == "Bearer access-1":
            return httpx.Response(
                401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
            )
        return httpx.Response(200, json={"path": path})


def _client(base_url: str, server: Server) -> AsyncClient:
    raw = httpx.AsyncClient(transport=httpx.MockTransport(server), headers={}, cookies={})
    return AsyncClient(base_url=base_url, handler=AsyncHttpxHandler(raw, owns_client=True))


def _identity(service: ShopLogin) -> Identity:
    return Identity(
        auth=(
            SHOP_SESSION.configure(
                credentials=LoginCredentials(username="ada", password=SecretStr("secret")),
                service=service,
            ),
        )
    )


@pytest.mark.asyncio
async def test_one_session_serves_two_services_over_one_client() -> None:
    server = Server()
    service = ShopLogin()
    client = _client(MAIN, server)
    sdk = ShopSdk(client, identity=_identity(service))

    await sdk.books.get()
    await sdk.card.get()
    await client.aclose()

    assert service.logins == 1
    assert server.calls == [
        (f"{MAIN}/session/login", ""),
        (f"{MAIN}/books", "Bearer access-1"),
        (f"{PAY}/v1/cards", "Bearer access-1"),
    ]


@pytest.mark.asyncio
async def test_one_session_serves_two_services_on_two_clients() -> None:
    from eazy_sdk import bind

    server = Server()
    service = ShopLogin()
    main = _client(MAIN, server)
    payments = _client(PAY, server)
    sdk = ShopSdk(
        main,
        bindings=(bind(CardApi, client=payments),),
        identity=_identity(service),
    )

    await sdk.books.get()
    await sdk.card.get()
    await main.aclose()
    await payments.aclose()

    assert service.logins == 1
    assert [url for url, _ in server.calls] == [
        f"{MAIN}/session/login",
        f"{MAIN}/books",
        f"{PAY}/v1/cards",
    ]
    assert server.calls[-1][1] == "Bearer access-1"


@pytest.mark.asyncio
async def test_a_401_on_a_service_that_never_issues_a_session_refreshes_and_replays() -> None:
    server = Server(expire_first=True)
    service = ShopLogin()
    client = _client(MAIN, server)
    sdk = ShopSdk(client, identity=_identity(service))

    response: NormalizedResponse[object] = await sdk.card.get()
    await client.aclose()

    assert response.status_code == 200
    assert service.logins == 1
    assert service.refreshes == 1
    assert [url for url, _ in server.calls] == [
        f"{MAIN}/session/login",
        f"{PAY}/v1/cards",
        f"{MAIN}/session/refresh",
        f"{PAY}/v1/cards",
    ]
    assert server.calls[-1][1] == "Bearer access-2"


@pytest.mark.asyncio
async def test_a_second_user_reuses_the_client_and_gets_its_own_session() -> None:
    server = Server()
    first = ShopLogin()
    second = ShopLogin()
    client = _client(MAIN, server)

    await ShopSdk(client, identity=_identity(first)).books.get()
    await ShopSdk(client, identity=_identity(second)).books.get()
    await client.aclose()

    assert (first.logins, second.logins) == (1, 1)
    assert [token for _, token in server.calls if token] == [
        "Bearer access-1",
        "Bearer access-2",
    ]


@pytest.mark.asyncio
async def test_a_root_may_declare_its_identity_on_the_class() -> None:
    service = ShopLogin()
    declared = _identity(service)

    class DeclaredSdk(ShopSdk):
        identity = declared

    server = Server()
    client = _client(MAIN, server)
    sdk = DeclaredSdk(client)
    await sdk.books.get()
    await client.aclose()

    assert service.logins == 1
    assert sdk._identity is declared


def test_the_constructor_overrides_a_declared_identity() -> None:
    declared = _identity(ShopLogin())
    supplied = _identity(ShopLogin())

    class DeclaredSdk(ShopSdk):
        identity = declared

    server = Server()
    client = _client(MAIN, server)
    assert DeclaredSdk(client, identity=supplied)._identity is supplied


def test_the_client_no_longer_configures_authentication() -> None:
    parameters = inspect.signature(ClientConfig).parameters
    for removed in ("auth", "key_provider", "dependencies", "observer"):
        assert removed not in parameters, removed
    assert not hasattr(ClientConfig(), "auth")
    for absent in ("bind_sdk", "_register_sdk_factory"):
        assert not hasattr(AsyncClient, absent), absent


def test_a_router_declares_its_identity_or_inherits_the_root_one() -> None:
    server = Server()
    client = _client(MAIN, server)
    identity = _identity(ShopLogin())
    sdk = ShopSdk(client, identity=identity)
    assert sdk.books._scope is sdk.card._scope
    assert sdk.books._scope is not AuthApi(client)._scope
    with pytest.raises(TypeError, match="one owner only"):
        BooksApi(client, identity=identity, scope=sdk.books._scope)


def test_a_scheme_outside_the_service_allowlist_is_rejected_at_assembly() -> None:
    other = session_scheme(UserSession, name="other-session")

    class LeakyApi(PaymentsService, AsyncApi):
        security = other

        @api.get("/leak", operation_id="leak", responses=Responses(success=()), raw_response=True)
        async def get(self) -> NormalizedResponse[object]:
            raise NotImplementedError

    class LeakySdk(AsyncRoot):
        leak = api_group(LeakyApi)

    server = Server()
    client = _client(MAIN, server)
    with pytest.raises(TypeError, match="allowlist"):
        LeakySdk(client)


@pytest.mark.asyncio
async def test_identity_carries_the_signing_key_and_the_dependency_registry() -> None:
    from eazy_sdk.dependencies import DependencyRegistry
    from eazy_sdk.request import SigningKey, SigningKeyRequirement

    def keys(_requirement: SigningKeyRequirement) -> SigningKey:
        return SigningKey(b"secret")

    registry = DependencyRegistry()
    identity = Identity(key_provider=keys, dependencies=registry)
    server = Server()
    client = _client(MAIN, server)
    router = BooksApi(client, identity=identity)

    assert router._scope.key_provider is keys
    assert router._scope.dependencies is registry
    await client.aclose()


def test_two_bindings_of_the_same_scheme_in_one_identity_are_rejected() -> None:
    from eazy_sdk.core.errors import PlanError

    first = SHOP_SESSION.configure(
        credentials=LoginCredentials(username="ada", password=SecretStr("a")),
        service=ShopLogin(),
    )
    second = SHOP_SESSION.configure(
        credentials=LoginCredentials(username="bob", password=SecretStr("b")),
        service=ShopLogin(),
    )
    server = Server()
    client = _client(MAIN, server)
    with pytest.raises(PlanError, match="twice"):
        BooksApi(client, identity=Identity(auth=(first, second)))


def test_identity_rejects_a_bare_auth_binding() -> None:
    binding = SHOP_SESSION.configure(
        credentials=LoginCredentials(username="ada", password=SecretStr("a")),
        service=ShopLogin(),
    )
    with pytest.raises(TypeError, match="tuple"):
        Identity(auth=binding)  # type: ignore[arg-type]
