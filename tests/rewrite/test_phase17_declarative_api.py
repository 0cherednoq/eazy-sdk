from __future__ import annotations

import inspect
import json
from typing import Annotated, Any, TypedDict, Unpack, assert_type, cast

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from eazy_sdk import ApiDefaults, AsyncApi, ClientConfig, SyncApi, api
from eazy_sdk._internal import PlanError, PlanNodeKind, WriterConflictError
from eazy_sdk.clients import CallOptions, RetryPolicy
from eazy_sdk.protection import (
    FromProtection,
    ProtectionRequirement,
    SolveContext,
    SolverBindings,
    bind_solver,
    protection_flow,
)
from eazy_sdk.request import BodyProjection, JsonBody, Path, Query
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


LOGIN_PROTECTION = ProtectionRequirement[ProtectionResult]("login-protection")


class CsrfResult(BaseModel):
    token: str


CSRF_PROTECTION = ProtectionRequirement[CsrfResult]("csrf")


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


LOGIN_BODY = BodyProjection(LoginPublic, _LoginWireBody, _login_wire, JsonBody())
CSRF_BODY = BodyProjection(LoginPublic, _CsrfWireBody, _csrf_wire, JsonBody())
INVALID_BODY = BodyProjection(LoginPublic, _InvalidWireBody, _invalid_wire, JsonBody())
NESTED_BODY = BodyProjection(
    LoginPublic,
    _NestedLoginWireBody,
    _nested_wire,
    JsonBody(),
)


USER_RESPONSES = Responses[User](success={200: Json(User), 201: Json(User)})
CHALLENGE_RESPONSES = Responses[Challenge](success={200: Json(Challenge)})
PROTECTION_RESPONSES = Responses[ProtectionResult](success={200: Json(ProtectionResult)})
CSRF_RESPONSES = Responses[CsrfResult](success={200: Json(CsrfResult)})


class AsyncUsersApi(AsyncApi):
    @api.get("/users/{user_id}", operation_id="getUser", responses=USER_RESPONSES)
    async def get_user(  # type: ignore[no-untyped-def]
        self,
        *,
        user_id: Annotated[int, Path()],
        include: Annotated[str | None, Query()] = None,
        options: CallOptions | None = None,
    ):
        raise AssertionError("declaration body must not execute")

    @api.post("/users", operation_id="createUser", responses=USER_RESPONSES)
    async def create_user(
        self,
        *,
        request: Annotated[CreateUser, JsonBody()],
    ) -> User:
        raise AssertionError("declaration body must not execute")


class SyncUsersApi(SyncApi):
    @api.get("/users/{user_id}", operation_id="getUserSync", responses=USER_RESPONSES)
    def get_user(  # type: ignore[no-untyped-def]
        self,
        *,
        user_id: Annotated[int, Path()],
        include: Annotated[str | None, Query()] = None,
        options: CallOptions | None = None,
    ):
        raise AssertionError("declaration body must not execute")


class ProtectionApi(AsyncApi):
    @api.get("/protection", operation_id="acquireProtection", responses=CHALLENGE_RESPONSES)
    async def acquire(self) -> Challenge:
        raise AssertionError("declaration body must not execute")

    @api.post("/protection/verify", operation_id="verifyProtection", responses=PROTECTION_RESPONSES)
    async def verify(
        self,
        *,
        answer: Annotated[ChallengeAnswer, JsonBody()],
    ) -> ProtectionResult:
        raise AssertionError("declaration body must not execute")

    @api.get("/csrf", operation_id="acquireCsrf", responses=CSRF_RESPONSES)
    async def csrf(self) -> CsrfResult:
        raise AssertionError("declaration body must not execute")


class AuthApi(AsyncApi):
    @api.post(
        "/login",
        operation_id="login",
        responses=USER_RESPONSES,
        protections=(LOGIN_PROTECTION,),
        body=LOGIN_BODY,
    )
    async def login(self, **request: Unpack[LoginPublic]) -> User:
        raise AssertionError("declaration body must not execute")

    @api.post(
        "/login/csrf",
        operation_id="loginCsrf",
        responses=USER_RESPONSES,
        protections=(CSRF_PROTECTION,),
        body=CSRF_BODY,
    )
    async def login_csrf(self, **request: Unpack[LoginPublic]) -> User:
        raise AssertionError("declaration body must not execute")

    @api.post(
        "/login/invalid",
        operation_id="loginInvalid",
        responses=USER_RESPONSES,
        protections=(LOGIN_PROTECTION,),
        body=INVALID_BODY,
    )
    async def login_invalid(self, **request: Unpack[LoginPublic]) -> User:
        raise AssertionError("declaration body must not execute")

    @api.post(
        "/login/nested",
        operation_id="loginNested",
        responses=USER_RESPONSES,
        protections=(LOGIN_PROTECTION,),
        body=NESTED_BODY,
        idempotent=True,
    )
    async def login_nested(self, **request: Unpack[LoginPublic]) -> User:
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
            @api.get("/users/{user_id}", responses=USER_RESPONSES)
            async def get_user(
                self, *, other: Annotated[int, Path()]
            ) -> User:
                raise NotImplementedError

    with pytest.raises(TypeError, match="options must be keyword-only"):

        class BadOptions(AsyncApi):
            @api.get("/users", responses=USER_RESPONSES)
            async def users(self, options: CallOptions | None = None) -> User:
                raise NotImplementedError

    with pytest.raises(TypeError, match="cannot infer its result type from responses"):

        class MissingResultType(SyncApi):
            @api.get("/empty", responses=Responses[object](success=()))
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
            operation_protections=(flow,),
            operation_protection_solvers=SolverBindings(
                bind_solver(LOGIN_PROTECTION, FakeSolver()),
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
            operation_protections=(
                protection_flow(
                    LOGIN_PROTECTION,
                    acquire=ProtectionApi.acquire,
                    solve=True,
                    verify=ProtectionApi.verify,
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
            operation_protections=(
                protection_flow(CSRF_PROTECTION, acquire=ProtectionApi.csrf),
            )
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
    compiled = descriptor.resolve(ApiDefaults()).compile()
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
            operation_protections=(
                protection_flow(
                    LOGIN_PROTECTION,
                    acquire=ProtectionApi.acquire,
                    solve=True,
                    verify=ProtectionApi.verify,
                ),
            ),
            operation_protection_solvers=SolverBindings(
                bind_solver(LOGIN_PROTECTION, FakeSolver()),
            ),
            retry=RetryPolicy.safe(max_attempts=2),
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

    projection = BodyProjection(LoginPublic, OverlappingTarget, project, JsonBody())

    class InvalidWriterApi(AsyncApi):
        @api.post(
            "/overlap",
            responses=USER_RESPONSES,
            protections=(LOGIN_PROTECTION,),
            body=projection,
        )
        async def overlap(self, **request: Unpack[LoginPublic]) -> User:
            raise NotImplementedError

    descriptor = cast(Any, InvalidWriterApi.overlap)
    with pytest.raises(PlanError, match="private wire writer paths overlap"):
        descriptor.resolve(ApiDefaults()).compile()


async def test_projection_cannot_prepopulate_a_reserved_private_path() -> None:
    requests: list[httpx.Request] = []

    def collide(source: LoginPublic) -> _CsrfWireBody:
        return cast(_CsrfWireBody, {**source, "csrf": "caller-value"})

    projection = BodyProjection(LoginPublic, _CsrfWireBody, collide, JsonBody())

    class CollisionApi(AsyncApi):
        @api.post(
            "/collision",
            responses=USER_RESPONSES,
            protections=(CSRF_PROTECTION,),
            body=projection,
        )
        async def collision(self, **request: Unpack[LoginPublic]) -> User:
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
            operation_protections=(
                protection_flow(CSRF_PROTECTION, acquire=ProtectionApi.csrf),
            )
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
            operation_protections=configured if invalid_mapping else ()
        ),
    )
    call = AuthApi(client).login_invalid if invalid_mapping else AuthApi(client).login
    with pytest.raises(TypeError, match=r"missing protection flow|has no field"):
        await call(email="ada", password="secret")
    await client.aclose()
    assert requests == []
