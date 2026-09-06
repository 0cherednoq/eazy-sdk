from __future__ import annotations

import inspect
import json
from typing import Annotated, Any, TypedDict, assert_type, cast

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from eazy_sdk import AsyncApi, ClientConfig, Resilience, Security, SyncApi, api
from eazy_sdk.clients import CallOptions, RetryPolicy
from eazy_sdk.core import (
    PlanError,
    PlanNodeKind,
    WriterConflictError,
)
from eazy_sdk.protection.advanced import (
    FromProtection,
    ProtectionBundle,
    SolveContext,
    SolverBindings,
    SolverRequirement,
    bind_solver,
    protection_flow,
)
from eazy_sdk.request import BodyProjection
from eazy_sdk.request.markers import JsonBody, Path, Query
from eazy_sdk.response import Json, ResponseEnvelope, Responses
from tests._support.zapros_clients import client_from_httpx


class User(BaseModel):
    id: int
    name: str


class CreateUser(BaseModel):
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginPublic(TypedDict):
    email: str
    password: str


class Challenge(BaseModel):
    challenge: str


class ChallengeAnswer(BaseModel):
    challenge: str
    answer: str


class ProtectionResult(BaseModel):
    challenge: str
    token: str


LOGIN_PROTECTION = SolverRequirement[Any, ProtectionResult]("login-protection")


class CsrfResult(BaseModel):
    token: str


CSRF_PROTECTION = SolverRequirement[Any, CsrfResult]("csrf")


class _LoginWireBody(LoginRequest):
    captcha_challenge: Annotated[
        str,
        FromProtection(LOGIN_PROTECTION, "challenge"),
    ]
    captcha_token: Annotated[str, FromProtection(LOGIN_PROTECTION, "token")]


class _CsrfWireBody(LoginRequest):
    csrf: Annotated[str, FromProtection(CSRF_PROTECTION, "token")]


class _InvalidWireBody(LoginRequest):
    captcha: Annotated[str, FromProtection(LOGIN_PROTECTION, "missing")]


class _NestedCaptchaWire(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    challenge: Annotated[
        str,
        FromProtection(LOGIN_PROTECTION, "challenge"),
        Field(serialization_alias="captchaChallenge"),
    ]
    token: Annotated[
        str,
        FromProtection(LOGIN_PROTECTION, "token"),
        Field(serialization_alias="captchaToken"),
    ]


class _NestedLoginWireBody(LoginRequest):
    model_config = ConfigDict(serialize_by_alias=True)

    captcha: _NestedCaptchaWire = Field(serialization_alias="security")


def _login_wire(source: LoginPublic) -> _LoginWireBody:
    return cast(_LoginWireBody, dict(source))


def _csrf_wire(source: LoginPublic) -> _CsrfWireBody:
    return cast(_CsrfWireBody, dict(source))


def _invalid_wire(source: LoginPublic) -> _InvalidWireBody:
    return cast(_InvalidWireBody, dict(source))


def _nested_wire(source: LoginPublic) -> _NestedLoginWireBody:
    return cast(_NestedLoginWireBody, dict(source))


LOGIN_BODY = BodyProjection(_LoginWireBody, _login_wire, JsonBody(), source=LoginPublic)
CSRF_BODY = BodyProjection(_CsrfWireBody, _csrf_wire, JsonBody(), source=LoginPublic)
INVALID_BODY = BodyProjection(_InvalidWireBody, _invalid_wire, JsonBody(), source=LoginPublic)
NESTED_BODY = BodyProjection(
    _NestedLoginWireBody,
    _nested_wire,
    JsonBody(),
    source=LoginPublic,
)


USER_RESPONSES = Responses[User](success={200: Json(User), 201: Json(User)})
CHALLENGE_RESPONSES = Responses[Challenge](success={200: Json(Challenge)})
PROTECTION_RESPONSES = Responses[ProtectionResult](success={200: Json(ProtectionResult)})
CSRF_RESPONSES = Responses[CsrfResult](success={200: Json(CsrfResult)})


class AsyncUsersApi(AsyncApi):
    @api.get(
        "/users/{user_id}",
        operation_id="getUser",
        success=USER_RESPONSES.success,
        errors=USER_RESPONSES.errors,
        fallback=USER_RESPONSES.fallback,
    )
    async def get_user(
        self,
        *,
        user_id: Annotated[int, Path()],
        include: Annotated[str | None, Query()] = None,
        options: CallOptions | None = None,
    ) -> User:
        raise AssertionError("declaration body must not execute")

    @api.post(
        "/users",
        operation_id="createUser",
        success=USER_RESPONSES.success,
        errors=USER_RESPONSES.errors,
        fallback=USER_RESPONSES.fallback,
    )
    async def create_user(
        self,
        *,
        request: Annotated[CreateUser, JsonBody()],
    ) -> User:
        raise AssertionError("declaration body must not execute")


class SyncUsersApi(SyncApi):
    @api.get(
        "/users/{user_id}",
        operation_id="getUserSync",
        success=USER_RESPONSES.success,
        errors=USER_RESPONSES.errors,
        fallback=USER_RESPONSES.fallback,
    )
    def get_user(
        self,
        *,
        user_id: Annotated[int, Path()],
        include: Annotated[str | None, Query()] = None,
        options: CallOptions | None = None,
    ) -> User:
        raise AssertionError("declaration body must not execute")


class ProtectionApi(AsyncApi):
    @api.get(
        "/protection",
        operation_id="acquireProtection",
        success=CHALLENGE_RESPONSES.success,
        errors=CHALLENGE_RESPONSES.errors,
        fallback=CHALLENGE_RESPONSES.fallback,
    )
    async def acquire(self) -> Challenge:
        raise AssertionError("declaration body must not execute")

    @api.post(
        "/protection/verify",
        operation_id="verifyProtection",
        success=PROTECTION_RESPONSES.success,
        errors=PROTECTION_RESPONSES.errors,
        fallback=PROTECTION_RESPONSES.fallback,
    )
    async def verify(
        self,
        *,
        answer: Annotated[ChallengeAnswer, JsonBody()],
    ) -> ProtectionResult:
        raise AssertionError("declaration body must not execute")

    @api.get(
        "/csrf",
        operation_id="acquireCsrf",
        success=CSRF_RESPONSES.success,
        errors=CSRF_RESPONSES.errors,
        fallback=CSRF_RESPONSES.fallback,
    )
    async def csrf(self) -> CsrfResult:
        raise AssertionError("declaration body must not execute")


class AuthApi(AsyncApi):
    @api.post(
        "/login",
        operation_id="login",
        success=USER_RESPONSES.success,
        errors=USER_RESPONSES.errors,
        fallback=USER_RESPONSES.fallback,
        protections=(LOGIN_PROTECTION,),
        projection=LOGIN_BODY,
    )
    async def login(self, *, email: str, password: str) -> User:
        raise AssertionError("declaration body must not execute")

    @api.post(
        "/login/csrf",
        operation_id="loginCsrf",
        success=USER_RESPONSES.success,
        errors=USER_RESPONSES.errors,
        fallback=USER_RESPONSES.fallback,
        protections=(CSRF_PROTECTION,),
        projection=CSRF_BODY,
    )
    async def login_csrf(self, *, email: str, password: str) -> User:
        raise AssertionError("declaration body must not execute")

    @api.post(
        "/login/invalid",
        operation_id="loginInvalid",
        success=USER_RESPONSES.success,
        errors=USER_RESPONSES.errors,
        fallback=USER_RESPONSES.fallback,
        protections=(LOGIN_PROTECTION,),
        projection=INVALID_BODY,
    )
    async def login_invalid(self, *, email: str, password: str) -> User:
        raise AssertionError("declaration body must not execute")

    @api.post(
        "/login/nested",
        operation_id="loginNested",
        success=USER_RESPONSES.success,
        errors=USER_RESPONSES.errors,
        fallback=USER_RESPONSES.fallback,
        protections=(LOGIN_PROTECTION,),
        idempotent=True,
        projection=NESTED_BODY,
    )
    async def login_nested(self, *, email: str, password: str) -> User:
        raise AssertionError("declaration body must not execute")


class FakeSolver:
    async def solve(
        self,
        challenge: Challenge,
        context: SolveContext,
    ) -> ChallengeAnswer:
        assert context.response is None
        return ChallengeAnswer(challenge=challenge.challenge, answer="solved")


async def test_async_method_and_with_response_use_the_shared_executor() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 42, "name": "Ada"})

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    api = AsyncUsersApi(client)

    user = await api.get_user(user_id=42, include="profile")
    envelope = await api.get_user.with_response(user_id=42)
    assert_type(user, User)
    assert_type(envelope, ResponseEnvelope[User, Any])
    await client.aclose()

    assert user == User(id=42, name="Ada")
    assert envelope.value == user
    assert [request.url.path for request in requests] == ["/users/42", "/users/42"]
    assert requests[0].url.query == b"include=profile"
    assert requests[1].url.query == b""


def test_sync_method_uses_the_same_operation_lowering() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 7, "name": "Grace"})

    client = client_from_httpx(
        httpx.Client(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(),
    )
    users = SyncUsersApi(client)
    user = users.get_user(user_id=7)
    client.close()

    assert_type(user, User)
    assert inspect.signature(users.get_user).return_annotation is User
    assert user.name == "Grace"
    assert requests[0].url.path == "/users/7"


def test_invalid_method_signatures_fail_during_class_creation() -> None:
    with pytest.raises(Exception, match="do not match template"):

        class BadPath(AsyncApi):
            @api.get(
                "/users/{user_id}",
                success=USER_RESPONSES.success,
                errors=USER_RESPONSES.errors,
                fallback=USER_RESPONSES.fallback,
            )
            async def get_user(
                self, *, other: Annotated[int, Path()]
            ) -> User:
                raise NotImplementedError

    with pytest.raises(TypeError, match="options must be keyword-only"):

        class BadOptions(AsyncApi):
            @api.get(
                "/users",
                success=USER_RESPONSES.success,
                errors=USER_RESPONSES.errors,
                fallback=USER_RESPONSES.fallback,
            )
            async def users(self, options: CallOptions | None = None) -> User:
                raise NotImplementedError

    with pytest.raises(TypeError, match="cannot infer its result type from responses"):

        class MissingResultType(SyncApi):
            @api.get("/empty", success=())
            def empty(self):  # type: ignore[no-untyped-def]
                raise NotImplementedError


async def test_mandatory_protection_verifies_and_injects_multiple_fields_atomically() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/protection":
            return httpx.Response(200, json={"challenge": "challenge-1"})
        if request.url.path == "/protection/verify":
            assert json.loads(request.content) == {
                "challenge": "challenge-1",
                "answer": "solved",
            }
            return httpx.Response(
                200,
                json={"challenge": "challenge-1", "token": "token-1"},
            )
        assert json.loads(request.content) == {
            "email": "ada@example.test",
            "password": "secret",
            "captcha_challenge": "challenge-1",
            "captcha_token": "token-1",
        }
        return httpx.Response(200, json={"id": 1, "name": "Ada"})

    flow = protection_flow(
        LOGIN_PROTECTION,
        acquire=ProtectionApi.acquire,
        solve=True,
        verify=ProtectionApi.verify,
    )
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            security=Security(
                ProtectionBundle(
                    operation_protections=(flow,),
                    solver_bindings=SolverBindings(
                            bind_solver(LOGIN_PROTECTION, FakeSolver()),
                        ),
                ),
            ),
        ),
    )
    user = await AuthApi(client).login(email="ada@example.test", password="secret")
    await client.aclose()

    assert user.name == "Ada"
    assert [request.url.path for request in requests] == [
        "/protection",
        "/protection/verify",
        "/login",
    ]


async def test_missing_protection_solver_fails_before_acquire_or_main_network() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            security=Security(
                ProtectionBundle(
                    operation_protections=(
                            protection_flow(
                                LOGIN_PROTECTION,
                                acquire=ProtectionApi.acquire,
                                solve=True,
                                verify=ProtectionApi.verify,
                            ),
                        ),
                ),
            ),
        ),
    )
    with pytest.raises(Exception, match="missing solver: login-protection"):
        await AuthApi(client).login(email="ada", password="secret")
    await client.aclose()

    assert requests == []


async def test_acquire_only_csrf_flow_injects_before_the_main_operation() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/csrf":
            return httpx.Response(200, json={"token": "csrf-1"})
        assert json.loads(request.content)["csrf"] == "csrf-1"
        return httpx.Response(200, json={"id": 1, "name": "Ada"})

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            security=Security(
                ProtectionBundle(
                    operation_protections=(
                            protection_flow(CSRF_PROTECTION, acquire=ProtectionApi.csrf),
                        ),
                ),
            ),
        ),
    )
    await AuthApi(client).login_csrf(email="ada", password="secret")
    await client.aclose()
    assert [request.url.path for request in requests] == ["/csrf", "/login/csrf"]


async def test_mandatory_protection_injects_nested_target_paths() -> None:
    requests: list[httpx.Request] = []
    main_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal main_attempts
        requests.append(request)
        if request.url.path == "/protection":
            return httpx.Response(200, json={"challenge": "challenge-nested"})
        if request.url.path == "/protection/verify":
            return httpx.Response(
                200,
                json={"challenge": "challenge-nested", "token": "token-nested"},
            )
        assert json.loads(request.content) == {
            "email": "nested@example.test",
            "password": "secret",
            "security": {
                "captchaChallenge": "challenge-nested",
                "captchaToken": "token-nested",
            },
        }
        main_attempts += 1
        if main_attempts == 1:
            return httpx.Response(503, json={"error": "retry"})
        return httpx.Response(200, json={"id": 2, "name": "Nested"})

    descriptor = cast(Any, AuthApi.login_nested)
    compiled = descriptor.resolve().compile()
    assert tuple(writer.path for writer in compiled.private_wire_writers) == (
        ("security", "captchaChallenge"),
        ("security", "captchaToken"),
    )
    assert PlanNodeKind.PRIVATE_WIRE in tuple(node.kind for node in compiled.plan.phases)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            resilience=Resilience(retry=RetryPolicy.safe(max_attempts=2)),
            security=Security(
                ProtectionBundle(
                    operation_protections=(
                            protection_flow(
                                LOGIN_PROTECTION,
                                acquire=ProtectionApi.acquire,
                                solve=True,
                                verify=ProtectionApi.verify,
                            ),
                        ),
                    solver_bindings=SolverBindings(
                            bind_solver(LOGIN_PROTECTION, FakeSolver()),
                        ),
                ),
            ),
        ),
    )
    user = await AuthApi(client).login_nested(
        email="nested@example.test",
        password="secret",
    )
    await client.aclose()

    assert user.name == "Nested"
    assert [request.url.path for request in requests] == [
        "/protection",
        "/protection/verify",
        "/login/nested",
        "/login/nested",
    ]


def test_overlapping_private_writer_paths_fail_during_compile() -> None:
    class OverlappingTarget(LoginRequest):
        captcha: Annotated[
            _NestedCaptchaWire,
            FromProtection(LOGIN_PROTECTION, "token"),
        ]

    def project(source: LoginPublic) -> OverlappingTarget:
        return cast(OverlappingTarget, dict(source))

    projection = BodyProjection(OverlappingTarget, project, JsonBody(), source=LoginPublic)

    class InvalidWriterApi(AsyncApi):
        @api.post(
            "/overlap",
            success=USER_RESPONSES.success,
            errors=USER_RESPONSES.errors,
            fallback=USER_RESPONSES.fallback,
            protections=(LOGIN_PROTECTION,),
            projection=projection,
        )
        async def overlap(self, *, email: str, password: str) -> User:
            raise NotImplementedError

    descriptor = cast(Any, InvalidWriterApi.overlap)
    with pytest.raises(PlanError, match="private wire writer paths overlap"):
        descriptor.resolve().compile()


async def test_projection_cannot_prepopulate_a_reserved_private_path() -> None:
    requests: list[httpx.Request] = []

    def collide(source: LoginPublic) -> _CsrfWireBody:
        return cast(_CsrfWireBody, {**source, "csrf": "caller-value"})

    projection = BodyProjection(_CsrfWireBody, collide, JsonBody(), source=LoginPublic)

    class CollisionApi(AsyncApi):
        @api.post(
            "/collision",
            success=USER_RESPONSES.success,
            errors=USER_RESPONSES.errors,
            fallback=USER_RESPONSES.fallback,
            protections=(CSRF_PROTECTION,),
            projection=projection,
        )
        async def collision(self, *, email: str, password: str) -> User:
            raise NotImplementedError

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/csrf":
            return httpx.Response(200, json={"token": "managed-value"})
        return httpx.Response(500)

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            security=Security(
                ProtectionBundle(
                    operation_protections=(
                            protection_flow(CSRF_PROTECTION, acquire=ProtectionApi.csrf),
                        ),
                ),
            ),
        ),
    )
    with pytest.raises(WriterConflictError, match="collide at 'csrf'"):
        await CollisionApi(client).collision(email="ada", password="secret")
    await client.aclose()

    assert [request.url.path for request in requests] == ["/csrf"]


@pytest.mark.parametrize("invalid_mapping", [False, True])
async def test_invalid_mandatory_protection_configuration_fails_before_network(
    invalid_mapping: bool,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    configured = (protection_flow(LOGIN_PROTECTION, acquire=ProtectionApi.acquire),)
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            security=Security(
                ProtectionBundle(
                    operation_protections=configured if invalid_mapping else (),
                ),
            ),
        ),
    )
    call = AuthApi(client).login_invalid if invalid_mapping else AuthApi(client).login
    with pytest.raises(TypeError, match=r"missing protection flow|has no field"):
        await call(email="ada", password="secret")
    await client.aclose()
    assert requests == []
