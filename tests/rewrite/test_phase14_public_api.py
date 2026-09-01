from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast

import httpx
import pytest
from pydantic import BaseModel, Field, SecretStr, field_validator

from eazy_sdk import (
    ApiDefaults,
    AsyncApi,
    ClientConfig,
    RetryPolicy,
    UnsafeReplayError,
    api,
)
from eazy_sdk.auth import (
    ApiKeyScheme,
    AuthContext,
    BasicScheme,
    Bearer,
    BearerScheme,
    CookieScheme,
    ExpiresAt,
    RefreshToken,
    ResolutionCycleError,
    SessionConfigurationError,
    session_auth,
    session_cookie,
    session_scheme,
)
from eazy_sdk.exceptions import HeaderValidationError
from eazy_sdk.request import (
    JsonBody,
    SigningKey,
    SigningKeyRequirement,
    header_output,
    hmac_sha256,
    method,
)
from eazy_sdk.response import (
    ApiError,
    Error,
    FromHeader,
    Json,
    NormalizedResponse,
    ResponseContext,
    Responses,
)
from eazy_sdk.response.cases import MalformedOutcome, SuccessOutcome
from tests._support.zapros_clients import client_from_httpx


class HeaderBackedResult(BaseModel):
    request_id: Annotated[str, FromHeader("X-Request-Id")] = Field(validation_alias="requestId")
    value: int

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        if not value.startswith("req-"):
            raise ValueError("invalid request id")
        return value.upper()


def _inspect_header_result(
    headers: tuple[tuple[str, str], ...],
    body: bytes = b'{"value": 7, "requestId": "body-must-not-win"}',
) -> SuccessOutcome[HeaderBackedResult] | MalformedOutcome:
    response: NormalizedResponse[object] = NormalizedResponse(
        status_code=200,
        url="https://api.example/jobs/7",
        method="GET",
        headers=(("Content-Type", "application/json"), *headers),
        body=body,
    )
    outcome = Responses[HeaderBackedResult](success={200: Json(HeaderBackedResult)}).inspect(
        ResponseContext(response)
    )
    assert isinstance(outcome, (SuccessOutcome, MalformedOutcome))
    return outcome


def test_from_header_supplies_a_general_response_field_before_pydantic_validation() -> None:
    outcome = _inspect_header_result((("x-ReQuEsT-iD", "req-42"),))

    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value == HeaderBackedResult.model_validate({"requestId": "req-42", "value": 7})
    assert outcome.value.request_id == "REQ-42"


def test_from_header_missing_required_field_is_a_typed_extraction_failure() -> None:
    outcome = _inspect_header_result(())

    assert isinstance(outcome, MalformedOutcome)
    assert isinstance(outcome.cause, HeaderValidationError)
    assert str(outcome.cause) == "Required response header 'X-Request-Id' is missing"


def test_from_header_rejects_repeated_field_lines_without_joining_them() -> None:
    outcome = _inspect_header_result((("X-Request-Id", "req-1"), ("x-request-id", "req-2")))

    assert isinstance(outcome, MalformedOutcome)
    assert isinstance(outcome.cause, HeaderValidationError)
    assert str(outcome.cause) == "Response header 'X-Request-Id' occurs more than once"


def test_from_header_leaves_value_validation_to_pydantic() -> None:
    outcome = _inspect_header_result((("X-Request-Id", "invalid"),))

    assert isinstance(outcome, MalformedOutcome)
    assert not isinstance(outcome.cause, HeaderValidationError)
    assert "invalid request id" in str(outcome.cause)


def test_from_header_has_no_parser_dsl_arguments() -> None:
    with pytest.raises(TypeError):
        FromHeader("Authorization", prefix="Bearer ")  # type: ignore[call-arg]


class LoginCredentials(BaseModel):
    username: str
    password: SecretStr


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool


class RefreshRequest(BaseModel):
    refresh_token: str


class UserSession(BaseModel):
    access_token: Annotated[SecretStr, Bearer()]
    refresh_token: Annotated[SecretStr, RefreshToken()]
    expires_at: Annotated[datetime, ExpiresAt(leeway=timedelta(seconds=10))]


class UserSessionApi(AsyncApi):
    @api.post(
        "/login",
        operation_id="login",
        responses=Responses(success={200: Json(UserSession)}),
    )
    async def login(self, *, body: Annotated[LoginRequest, JsonBody()]) -> UserSession:
        raise NotImplementedError

    @api.post(
        "/refresh",
        operation_id="refresh",
        responses=Responses(success={200: Json(UserSession)}),
    )
    async def refresh(self, *, body: Annotated[RefreshRequest, JsonBody()]) -> UserSession:
        raise NotImplementedError


class UserSessionSdk:
    def __init__(self, client: Any) -> None:
        self.auth = UserSessionApi(client)


class UserAuthService:
    async def acquire(
        self,
        credentials: LoginCredentials,
        context: AuthContext[UserSessionSdk],
    ) -> UserSession:
        request = LoginRequest(
            username=credentials.username,
            password=credentials.password.get_secret_value(),
            remember_me=True,
        )
        return await context.sdk.auth.login(body=request)

    async def refresh(
        self,
        session: UserSession,
        context: AuthContext[UserSessionSdk],
    ) -> UserSession:
        request = RefreshRequest(refresh_token=session.refresh_token.get_secret_value())
        return await context.sdk.auth.refresh(body=request)


class RecursiveAuthApi(AsyncApi):
    @api.post(
        "/recursive-login",
        operation_id="recursiveLogin",
        responses=Responses(success=()),
        raw_response=True,
    )
    async def login(self) -> NormalizedResponse[object]:
        raise NotImplementedError


class RecursiveAccountApi(AsyncApi):
    @api.get(
        "/recursive-account",
        operation_id="recursiveAccount",
        responses=Responses(success=()),
        raw_response=True,
    )
    async def get(self) -> NormalizedResponse[object]:
        raise NotImplementedError


class RecursiveSdk:
    def __init__(self, client: Any, security: object) -> None:
        defaults = ApiDefaults(security=security)  # type: ignore[arg-type]
        self.auth = RecursiveAuthApi(client)
        self.auth.defaults = defaults
        self.account = RecursiveAccountApi(client)
        self.account.defaults = defaults


class RecursiveAuthService:
    async def acquire(
        self,
        _credentials: LoginCredentials,
        context: AuthContext[RecursiveSdk],
    ) -> UserSession:
        return cast(UserSession, await context.sdk.auth.login())


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2030, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def _protected_call(
    client: Any,
    security: object,
    *,
    sdk: object | None = None,
    method_name: str = "GET",
    path: str = "/account",
    signing: tuple[object, ...] = (),
) -> NormalizedResponse[object]:
    decorator = api.get if method_name == "GET" else api.post

    class AccountApi(AsyncApi):
        @decorator(
            path,
            operation_id="account",
            responses=Responses(success=()),
            security=security,
            signing=signing,
            raw_response=True,
        )
        async def account(self) -> NormalizedResponse[object]:
            raise NotImplementedError

    sdk_type = type(sdk) if sdk is not None else UserSessionSdk
    client.bind_sdk(sdk_type)
    return await AccountApi(client).account()


def _sync_protected_call(client: Any, security: object) -> NormalizedResponse[object]:
    from eazy_sdk import SyncApi

    class AccountApi(SyncApi):
        @api.get(
            "/account",
            operation_id="account",
            responses=Responses(success=()),
            security=security,
            raw_response=True,
        )
        def account(self) -> NormalizedResponse[object]:
            raise NotImplementedError

    return AccountApi(client).account()


async def test_session_auth_annotations_drive_login_reuse_expiry_and_refresh() -> None:
    clock = FakeClock()
    calls: list[tuple[str, str]] = []
    issued = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        authorization = request.headers.get("Authorization", "")
        calls.append((request.url.path, authorization))
        if request.url.path == "/login":
            assert json.loads(request.content) == {
                "username": "ada@example.test",
                "password": "correct-horse",
                "remember_me": True,
            }
            issued += 1
        elif request.url.path == "/refresh":
            assert json.loads(request.content) == {"refresh_token": f"refresh-{issued}"}
            issued += 1
        else:
            return httpx.Response(200, json={"authorization": authorization})
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{issued}",
                "refresh_token": f"refresh-{issued}",
                "expires_at": (clock.now + timedelta(minutes=5)).isoformat(),
            },
        )

    credentials = LoginCredentials(
        username="ada@example.test",
        password=SecretStr("correct-horse"),
    )
    auth = session_auth(
        UserSession,
        credentials=credentials,
        service=UserAuthService(),
        clock=clock,
    )
    raw = httpx.AsyncClient(
        base_url="https://api.example",
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    client = client_from_httpx(raw, config=ClientConfig(auth=auth))

    first = await _protected_call(client, auth.scheme)
    reused = await _protected_call(client, auth.scheme)
    clock.now += timedelta(seconds=295)
    refreshed = await _protected_call(client, auth.scheme)
    await client.aclose()

    assert first.json() == reused.json() == {"authorization": "Bearer access-1"}
    assert refreshed.json() == {"authorization": "Bearer access-2"}
    assert [path for path, _ in calls].count("/login") == 1
    assert [path for path, _ in calls].count("/refresh") == 1
    assert type(credentials) is LoginCredentials


async def test_bound_auth_sdk_propagates_the_lifecycle_graph_before_network_io() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    auth = session_auth(
        UserSession,
        credentials=LoginCredentials(username="ada", password=SecretStr("secret")),
        service=RecursiveAuthService(),
    )
    raw = httpx.AsyncClient(
        base_url="https://api.example",
        transport=httpx.MockTransport(handler),
        headers={},
        cookies={},
    )
    client = client_from_httpx(raw, config=ClientConfig(auth=auth))
    sdk = client.bind_sdk(lambda scoped: RecursiveSdk(scoped, auth.scheme))

    with pytest.raises(
        ResolutionCycleError,
        match=r"acquire session.*acquire session",
    ):
        await sdk.account.get()
    await client.aclose()

    assert calls == 0


async def test_session_auth_refreshes_a_selected_session_after_401_and_replays() -> None:
    clock = FakeClock()
    calls: list[tuple[str, str]] = []
    issued = 0
    account_signatures: list[str] = []
    key_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        authorization = request.headers.get("Authorization", "")
        calls.append((request.url.path, authorization))
        if request.url.path == "/account":
            account_signatures.append(request.headers["X-Signature"])
        if request.url.path == "/login":
            issued = 1
        elif request.url.path == "/refresh":
            assert json.loads(request.content) == {"refresh_token": "refresh-1"}
            issued = 2
        elif authorization == "Bearer access-1":
            return httpx.Response(401, json={"error": "expired"})
        else:
            return httpx.Response(200, json={"authorization": authorization})
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{issued}",
                "refresh_token": f"refresh-{issued}",
                "expires_at": (clock.now + timedelta(minutes=5)).isoformat(),
            },
        )

    auth = session_auth(
        UserSession,
        credentials=LoginCredentials(username="ada", password=SecretStr("secret")),
        service=UserAuthService(),
        clock=clock,
    )

    def key_provider(_requirement: SigningKeyRequirement) -> SigningKey:
        nonlocal key_calls
        key_calls += 1
        return SigningKey(f"auth-refresh-{key_calls}".encode())

    signature = hmac_sha256(
        key=SigningKeyRequirement("auth-refresh-key"),
        base=method(),
        output=header_output("X-Signature"),
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth, key_provider=key_provider),
    )

    response = await _protected_call(client, auth.scheme, signing=(signature,))
    await client.aclose()

    assert response.json() == {"authorization": "Bearer access-2"}
    assert [path for path, _ in calls] == ["/login", "/account", "/refresh", "/account"]
    assert key_calls == 2
    assert len(set(account_signatures)) == 2


async def test_session_auth_acquire_is_singleflight_on_concurrent_first_use() -> None:
    import asyncio

    clock = FakeClock()
    login_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls
        if request.url.path == "/login":
            login_calls += 1
            await asyncio.sleep(0)
            return httpx.Response(
                200,
                json={
                    "access_token": "shared",
                    "refresh_token": "refresh",
                    "expires_at": (clock.now + timedelta(minutes=5)).isoformat(),
                },
            )
        return httpx.Response(200, json={"authorization": request.headers.get("Authorization")})

    auth = session_auth(
        UserSession,
        credentials=LoginCredentials(username="ada", password=SecretStr("secret")),
        service=UserAuthService(),
        clock=clock,
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth),
    )

    first, second = await asyncio.gather(
        _protected_call(client, auth.scheme),
        _protected_call(client, auth.scheme),
    )
    await client.aclose()

    assert first.json() == second.json() == {"authorization": "Bearer shared"}
    assert login_calls == 1


def test_session_roles_are_validated_before_any_io() -> None:
    class InvalidSession(BaseModel):
        first: Annotated[SecretStr, Bearer()]
        second: Annotated[SecretStr, Bearer()]

    credentials = LoginCredentials(username="ada", password=SecretStr("secret"))
    with pytest.raises(SessionConfigurationError, match="exactly one Bearer"):
        session_auth(
            InvalidSession,
            credentials=credentials,
            service=UserAuthService(),
        )


def test_auth_service_contract_is_validated_during_configuration() -> None:
    credentials = LoginCredentials(username="ada", password=SecretStr("secret"))

    class MissingAcquire:
        pass

    class NonCallableAcquire:
        acquire = "login"

    class SyncAcquire:
        def acquire(self, credentials: object, context: object) -> UserSession:
            raise AssertionError

    class SyncRefresh:
        async def acquire(self, credentials: object, context: object) -> UserSession:
            raise AssertionError

        def refresh(self, session: object, context: object) -> UserSession:
            raise AssertionError

    class NonCallableRefresh:
        refresh = "refresh"

        async def acquire(self, credentials: object, context: object) -> UserSession:
            raise AssertionError

    class WrongAcquireSignature:
        async def acquire(self, credentials: object) -> UserSession:
            raise AssertionError

    with pytest.raises(
        SessionConfigurationError,
        match=r"MissingAcquire must define async acquire\(credentials, context\)",
    ):
        session_auth(
            UserSession,
            credentials=credentials,
            service=cast(Any, MissingAcquire()),
        )

    with pytest.raises(
        SessionConfigurationError,
        match=r"NonCallableAcquire\.acquire must be callable",
    ):
        session_scheme(UserSession).configure(
            credentials=credentials,
            service=cast(Any, NonCallableAcquire()),
        )

    with pytest.raises(
        SessionConfigurationError,
        match=r"SyncAcquire\.acquire must be declared with async def",
    ):
        session_auth(
            UserSession,
            credentials=credentials,
            service=cast(Any, SyncAcquire()),
        )

    with pytest.raises(
        SessionConfigurationError,
        match=r"SyncRefresh\.refresh must be declared with async def",
    ):
        session_auth(
            UserSession,
            credentials=credentials,
            service=cast(Any, SyncRefresh()),
        )

    with pytest.raises(
        SessionConfigurationError,
        match=r"NonCallableRefresh\.refresh must be callable or omitted",
    ):
        session_auth(
            UserSession,
            credentials=credentials,
            service=cast(Any, NonCallableRefresh()),
        )

    with pytest.raises(
        SessionConfigurationError,
        match=(
            r"WrongAcquireSignature\.acquire must accept "
            r"\(credentials, context\) as positional arguments"
        ),
    ):
        session_auth(
            UserSession,
            credentials=credentials,
            service=cast(Any, WrongAcquireSignature()),
        )


def test_auth_service_may_omit_optional_refresh() -> None:
    class AcquireOnlyService:
        async def acquire(
            self,
            credentials: LoginCredentials,
            context: AuthContext[UserSessionSdk],
        ) -> UserSession:
            raise AssertionError

    auth = session_auth(
        UserSession,
        credentials=LoginCredentials(username="ada", password=SecretStr("secret")),
        service=AcquireOnlyService(),
    )

    assert auth.scheme.diagnostic_name == "session"


class HeaderUserSession(BaseModel):
    access_token: Annotated[SecretStr, FromHeader("X-Access-Token"), Bearer()]
    refresh_token: Annotated[SecretStr, RefreshToken()]
    expires_at: Annotated[datetime, ExpiresAt()]


class HeaderSessionApi(AsyncApi):
    @api.post(
        "/header-login",
        operation_id="headerLogin",
        responses=Responses(success={200: Json(HeaderUserSession)}),
    )
    async def login(self, *, body: Annotated[LoginRequest, JsonBody()]) -> HeaderUserSession:
        raise NotImplementedError


class HeaderSessionSdk:
    def __init__(self, client: Any) -> None:
        self.auth = HeaderSessionApi(client)


class HeaderAuthService:
    async def acquire(
        self, credentials: LoginCredentials, context: AuthContext[HeaderSessionSdk]
    ) -> HeaderUserSession:
        return await context.sdk.auth.login(
            body=LoginRequest(
                username=credentials.username,
                password=credentials.password.get_secret_value(),
                remember_me=True,
            )
        )


async def test_session_token_can_come_from_an_exact_response_header() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/header-login":
            return httpx.Response(
                200,
                headers={"X-Access-Token": "header-token"},
                json={
                    "refresh_token": "refresh",
                    "expires_at": "2031-01-01T00:00:00Z",
                },
            )
        return httpx.Response(200, json={"authorization": request.headers.get("Authorization")})

    auth = session_auth(
        HeaderUserSession,
        credentials=LoginCredentials(username="ada", password=SecretStr("secret")),
        service=HeaderAuthService(),
        clock=lambda: datetime(2030, 1, 1, tzinfo=UTC),
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth),
    )
    response = await _protected_call(client, auth.scheme, sdk=HeaderSessionSdk(client))
    await client.aclose()

    assert response.json() == {"authorization": "Bearer header-token"}


class CookieLoginResult(BaseModel):
    authenticated: bool


class CookieSessionApi(AsyncApi):
    @api.post(
        "/cookie-login",
        operation_id="cookieLogin",
        responses=Responses(success={200: Json(CookieLoginResult)}),
    )
    async def login(self, *, body: Annotated[LoginRequest, JsonBody()]) -> CookieLoginResult:
        raise NotImplementedError


class CookieSessionSdk:
    def __init__(self, client: Any) -> None:
        self.auth = CookieSessionApi(client)


class CookieAuthService:
    async def acquire(
        self,
        credentials: LoginCredentials,
        context: AuthContext[CookieSessionSdk],
    ) -> CookieLoginResult:
        return context.capture(
            await context.sdk.auth.login.with_response(
                body=LoginRequest(
                    username=credentials.username,
                    password=credentials.password.get_secret_value(),
                    remember_me=False,
                )
            )
        )


async def test_session_cookie_captures_set_cookie_without_model_annotations() -> None:
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("Cookie", "")
        calls.append((request.url.path, cookie))
        if request.url.path == "/cookie-login":
            return httpx.Response(
                200,
                json={"authenticated": True},
                headers=[
                    ("Set-Cookie", "csrf=csrf-1; Path=/; HttpOnly"),
                    ("Set-Cookie", "session_id=session-1; Path=/; HttpOnly; SameSite=Lax"),
                ],
            )
        return httpx.Response(200, json={"cookie": cookie})

    credentials = LoginCredentials(username="ada", password=SecretStr("secret"))
    auth = session_cookie(
        "session_id",
        credentials=credentials,
        service=CookieAuthService(),
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth),
    )

    first = await _protected_call(client, auth.scheme, sdk=CookieSessionSdk(client))
    second = await _protected_call(client, auth.scheme, sdk=CookieSessionSdk(client))
    await client.aclose()

    assert first.json() == second.json() == {"cookie": "session_id=session-1"}
    assert [path for path, _ in calls].count("/cookie-login") == 1


async def test_session_cookie_rotates_with_attributes_and_rolls_back_deletion() -> None:
    login_calls = 0
    protected_cookies: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls
        if request.url.path == "/cookie-login":
            login_calls += 1
            if login_calls == 1:
                return httpx.Response(
                    200,
                    json={"authenticated": True},
                    headers={
                        "Set-Cookie": (
                            "session_id=first; Domain=api.example; Path=/; Max-Age=3600; "
                            "Secure; HttpOnly; SameSite=Strict"
                        )
                    },
                )
            return httpx.Response(
                200,
                json={"authenticated": True},
                headers={"Set-Cookie": "session_id=; Path=/; Max-Age=0"},
            )
        cookie = request.headers.get("Cookie", "")
        protected_cookies.append(cookie)
        return httpx.Response(401, json={"error": "expired"})

    auth = session_cookie(
        "session_id",
        credentials=LoginCredentials(username="ada", password=SecretStr("secret")),
        service=CookieAuthService(),
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth),
    )

    with pytest.raises(SessionConfigurationError, match="active Set-Cookie"):
        await _protected_call(client, auth.scheme, sdk=CookieSessionSdk(client))
    with pytest.raises(SessionConfigurationError, match="active Set-Cookie"):
        await _protected_call(client, auth.scheme, sdk=CookieSessionSdk(client))
    await client.aclose()

    assert protected_cookies == ["session_id=first", "session_id=first"]
    assert login_calls == 3


async def test_static_scheme_helper_avoids_public_provider_registry() -> None:
    scheme = BearerScheme("user")
    auth = scheme.static("ready-token")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"authorization": request.headers.get("Authorization")},
        )

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth),
    )
    response = await _protected_call(client, scheme)
    await client.aclose()

    assert response.json() == {"authorization": "Bearer ready-token"}
    assert "ready-token" not in repr(auth)


@pytest.mark.parametrize(
    ("scheme", "value", "expected"),
    [
        (BasicScheme(), ("ada", "secret"), ("authorization", "Basic YWRhOnNlY3JldA==")),
        (ApiKeyScheme.header("X-API-Key"), "key", ("x-api-key", "key")),
        (CookieScheme("sid"), "cookie", ("cookie", "sid=cookie")),
    ],
)
async def test_every_static_scheme_uses_the_short_binding_path(
    scheme: object, value: object, expected: tuple[str, str]
) -> None:
    typed_scheme = scheme  # keep parametrization readable
    auth = typed_scheme.static(value)  # type: ignore[attr-defined]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": request.headers.get(expected[0])})

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth),
    )
    response = await _protected_call(client, auth.scheme)
    await client.aclose()

    assert response.json() == {"value": expected[1]}


def test_public_clients_accept_zapros_handlers_without_catch_all_options() -> None:
    from eazy_sdk import AsyncClient, Client

    for client_type in (Client, AsyncClient):
        signature = inspect.signature(client_type)
        assert tuple(signature.parameters) == (
            "base_url",
            "handler",
            "config",
            "owns_handler",
            "profile",
        )
        assert all(
            parameter.kind is not inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )


def test_public_namespace_exposes_one_client_path_and_hides_runtime_records() -> None:
    import importlib.util

    import eazy_sdk
    import eazy_sdk.clients as clients_api
    import eazy_sdk.codegen as codegen_api

    assert {
        "Client",
        "AsyncClient",
        "HandlerProfile",
        "ModelAdapter",
        "BodyCodec",
        "api",
    } <= set(eazy_sdk.__all__)
    verbs = {
        "delete",
        "get",
        "head",
        "options",
        "patch",
        "post",
        "put",
        "request",
        "trace",
    }
    assert eazy_sdk.api is api
    assert {name for name in dir(api) if not name.startswith("_")} == verbs
    for verb in verbs:
        assert callable(getattr(api, verb))
        if verb != "request":  # the package also exposes the eazy_sdk.request module
            assert not hasattr(eazy_sdk, verb)
        assert not hasattr(codegen_api, verb)
    assert set(clients_api.__all__) == {
        "AsyncClient",
        "AttemptLimitExceeded",
        "CallOptions",
        "Client",
        "ClientConfig",
        "RedirectLimitExceeded",
        "RetryPolicy",
        "UnsafeReplayError",
    }
    assert {"Client", "AsyncClient"} <= set(codegen_api.__all__)
    assert importlib.util.find_spec("eazy_sdk.adapters") is None
    for namespace in (eazy_sdk, clients_api, codegen_api):
        for removed in (
            "DumpPolicy",
            "ValidatedSyncClient",
            "ValidatedAsyncClient",
            "wrap_httpx",
            "wrap_requests",
            "wrap_curl_cffi",
            "wrap_wreq",
        ):
            assert not hasattr(namespace, removed)
    for hidden in ("ExecutionCore", "ExecutionResult", "ExecutionRuntime"):
        assert not hasattr(clients_api, hidden)


async def test_retry_policy_safe_retries_status_with_bounded_deterministic_backoff() -> None:
    attempts = 0
    delays: list[float] = []
    observed: list[tuple[str, object | None]] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            503 if attempts < 3 else 200,
            json={"attempt": attempts},
        )

    policy = RetryPolicy.safe(
        max_attempts=3,
        base_delay=0.1,
        max_delay=0.15,
        jitter=0.05,
        sleep=sleep,
        random_source=lambda: 0.5,
    )
    config = ClientConfig(
        retry=policy,
        auth_retries=0,
        observer=lambda phase, value: observed.append((phase, value)),
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=config,
    )

    response = await _protected_call(client, None)
    await client.aclose()

    assert response.status_code == 200
    assert attempts == 3
    assert delays == pytest.approx([0.125, 0.175])
    assert [phase for phase, _ in observed].count("prepared") == 3


async def test_retry_policy_rejects_unsafe_operation_without_idempotency_proof() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "busy"})

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            retry=RetryPolicy.safe(max_attempts=2),
            auth_retries=0,
        ),
    )
    with pytest.raises(UnsafeReplayError, match="idempotent"):
        await _protected_call(client, None, method_name="POST")
    await client.aclose()


async def test_retry_none_emits_exactly_once() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"error": "busy"})

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(retry=RetryPolicy.none(), auth_retries=0),
    )
    response = await _protected_call(client, None)
    await client.aclose()

    assert response.status_code == 503
    assert attempts == 1


class BusyProblem(BaseModel):
    code: str


class BusyError(ApiError[BusyProblem]):
    pass


async def test_retry_exhaustion_uses_the_contract_typed_terminal_error() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"code": "busy"})

    class EventuallyApi(AsyncApi):
        @api.get(
            "/eventually",
            operation_id="eventuallyAvailable",
            responses=Responses(
                success={200: Json(HeaderBackedResult)},
                errors=(Error(503, Json(BusyProblem), exception=BusyError),),
            ),
        )
        async def eventually(self) -> HeaderBackedResult:
            raise NotImplementedError

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            retry=RetryPolicy.safe(max_attempts=3),
            auth_retries=0,
        ),
    )

    with pytest.raises(BusyError) as captured:
        await EventuallyApi(client).eventually()
    await client.aclose()

    assert captured.value.error == BusyProblem(code="busy")
    assert attempts == 3


async def test_auth_refresh_and_response_retry_keep_independent_budgets() -> None:
    clock = FakeClock()
    account_calls = 0
    issued = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal account_calls, issued
        if request.url.path == "/login":
            issued = 1
        elif request.url.path == "/refresh":
            issued = 2
        else:
            account_calls += 1
            if request.headers.get("Authorization") == "Bearer access-1":
                return httpx.Response(401, json={"error": "expired"})
            if account_calls == 2:
                return httpx.Response(503, json={"error": "busy"})
            return httpx.Response(200, json={"attempt": account_calls})
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{issued}",
                "refresh_token": f"refresh-{issued}",
                "expires_at": (clock.now + timedelta(minutes=5)).isoformat(),
            },
        )

    auth = session_auth(
        UserSession,
        credentials=LoginCredentials(username="ada", password=SecretStr("secret")),
        service=UserAuthService(),
        clock=clock,
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth, retry=RetryPolicy.safe(max_attempts=2)),
    )
    response = await _protected_call(client, auth.scheme)
    await client.aclose()

    assert response.json() == {"attempt": 3}
    assert account_calls == 3


async def test_response_retry_reprepares_and_resigns_every_attempt() -> None:
    key_calls = 0
    signatures: list[str] = []

    def key_provider(_requirement: SigningKeyRequirement) -> SigningKey:
        nonlocal key_calls
        key_calls += 1
        return SigningKey(f"secret-{key_calls}")

    signature = hmac_sha256(
        key=SigningKeyRequirement("retry-key"),
        base=method(),
        output=header_output("X-Signature"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        signatures.append(request.headers["X-Signature"])
        return httpx.Response(503 if len(signatures) < 3 else 200)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            retry=RetryPolicy.safe(max_attempts=3),
            auth_retries=0,
            key_provider=key_provider,
        ),
    )
    response = await _protected_call(client, None, path="/signed", signing=(signature,))
    await client.aclose()

    assert response.status_code == 200
    assert key_calls == 3
    assert len(set(signatures)) == 3


def test_sync_and_async_factories_share_the_same_immutable_config_schema() -> None:
    import asyncio

    attempts = {"sync": 0, "async": 0}
    config = ClientConfig(retry=RetryPolicy.safe(max_attempts=2), auth_retries=0)

    def sync_handler(_request: httpx.Request) -> httpx.Response:
        attempts["sync"] += 1
        return httpx.Response(503 if attempts["sync"] == 1 else 200)

    async def run_async() -> int:
        async def async_handler(_request: httpx.Request) -> httpx.Response:
            attempts["async"] += 1
            return httpx.Response(503 if attempts["async"] == 1 else 200)

        client = client_from_httpx(
            httpx.AsyncClient(
                base_url="https://api.example",
                transport=httpx.MockTransport(async_handler),
                headers={},
                cookies={},
            ),
            config=config,
        )
        response = await _protected_call(client, None)
        await client.aclose()
        return response.status_code

    sync_client = client_from_httpx(
        httpx.Client(
            base_url="https://api.example",
            transport=httpx.MockTransport(sync_handler),
            headers={},
            cookies={},
        ),
        config=config,
    )
    sync_response = _sync_protected_call(sync_client, None)
    sync_client.close()

    assert sync_response.status_code == asyncio.run(run_async()) == 200
    assert attempts == {"sync": 2, "async": 2}


def test_transport_options_and_runtime_policy_have_separate_typed_boundaries() -> None:
    from eazy_sdk.handlers.curl_cffi import CurlCffiZaprosHandler

    config_parameters = inspect.signature(ClientConfig).parameters
    curl_parameters = inspect.signature(CurlCffiZaprosHandler).parameters

    assert "base_url" not in config_parameters
    assert "impersonate" not in config_parameters
    assert "impersonate" in curl_parameters
    assert "retry" not in curl_parameters
