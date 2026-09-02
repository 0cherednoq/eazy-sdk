from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict, assert_type, cast

import pytest

from eazy_sdk import (
    ApiDefaults,
    AsyncApi,
    AsyncSdk,
    Client,
    PreparationIncomplete,
    PreparedCall,
    PrepareOptions,
    SyncApi,
    SyncSdk,
    api,
    api_group,
)
from eazy_sdk._internal.http_plan import RequestScope
from eazy_sdk._internal.kernel import ParsedValue
from eazy_sdk.auth import BearerScheme
from eazy_sdk.auth.core import AuthProviderIdentity, AuthProviders, StaticAuthProvider
from eazy_sdk.clients.async_client import _AsyncClientCore
from eazy_sdk.clients.executor import ExecutionRuntime
from eazy_sdk.clients.sync_client import _SyncClientCore
from eazy_sdk.handlers import (
    AutomaticHeaderPolicy,
    CapabilityLevel,
    HandlerProfile,
    RedirectControl,
)
from eazy_sdk.protection.advanced import (
    ChallengeSolverBindings,
    SolveContext,
    SolverRequirement,
    before_call_policy,
    bind_challenge_solver,
    private_bindings,
    private_header,
    until_rejected,
)
from eazy_sdk.request import BodyProjection, Cookie, JsonBody, Path, Query
from eazy_sdk.request.prepared import HttpProtocol
from eazy_sdk.response import (
    ApiError,
    Error,
    Json,
    NormalizedResponse,
    Parsed,
    ResponseContext,
    Responses,
    Success,
    callable_parser,
)
from eazy_sdk.response.cases import SuccessOutcome
from eazy_sdk.testing import AsyncRecordingHandler, RecordingHandler


@dataclass(frozen=True, slots=True)
class User:
    name: str


@dataclass(frozen=True, slots=True)
class Problem:
    detail: str


COMMON_ERROR = Error(500, Json(Problem))
LOCAL_ERROR = Error(404, Json(Problem), exception=ApiError)


def _regular_html(context: ResponseContext[object]) -> bool:
    return context.response.status_code == 200


class ShorthandApi(SyncApi):
    defaults = ApiDefaults(errors=(COMMON_ERROR,))

    @api.get(
        "/users/{user_id}",
        response=Json(status=201, when=_regular_html),
        errors=(LOCAL_ERROR,),
    )
    def get_user(self, *, user_id: Annotated[int, Path()]) -> User:
        raise NotImplementedError

    @api.get(
        "/users/{user_id}/isolated",
        response=Json(),
        errors=(LOCAL_ERROR,),
        inherit_errors=False,
    )
    def isolated(self, *, user_id: Annotated[int, Path()]) -> User:
        raise NotImplementedError


CAPABILITIES = HandlerProfile(
    protocols=frozenset({HttpProtocol.HTTP_1_1}),
    exact_target=CapabilityLevel.CAPTURE_VERIFIED,
    header_order=CapabilityLevel.CAPTURE_VERIFIED,
    header_casing=CapabilityLevel.CAPTURE_VERIFIED,
    duplicate_headers=CapabilityLevel.CAPTURE_VERIFIED,
    preencoded_body=CapabilityLevel.CAPTURE_VERIFIED,
    manual_cookie_field=CapabilityLevel.CAPTURE_VERIFIED,
    automatic_headers=AutomaticHeaderPolicy.MATERIALIZED,
    redirects=RedirectControl.FORCED_OFF,
    replayable_streams=CapabilityLevel.CAPTURE_VERIFIED,
)


class PrepareApi(SyncApi):
    @api.post("/users", response=Json())
    def create(
        self,
        *,
        page: Annotated[int, Query()] = 1,
        session: Annotated[str, Cookie()] = "secret",
        body: Annotated[dict[str, str], JsonBody()],
    ) -> User:
        raise NotImplementedError


class AsyncPrepareApi(AsyncApi):
    @api.post("/users", response=Json())
    async def create(
        self,
        *,
        page: Annotated[int, Query()] = 1,
        body: Annotated[dict[str, str], JsonBody()],
    ) -> User:
        raise NotImplementedError


class RootUsersApi(SyncApi):
    @api.get("/users/{user_id}", response=Json())
    def get(self, *, user_id: Annotated[int, Path()]) -> User:
        raise NotImplementedError


class AsyncRootUsersApi(AsyncApi):
    @api.get("/users/{user_id}", response=Json())
    async def get(self, *, user_id: Annotated[int, Path()]) -> User:
        raise NotImplementedError


class StoreSdk(SyncSdk):
    users = api_group(RootUsersApi)


class AsyncStoreSdk(AsyncSdk):
    users = api_group(AsyncRootUsersApi)


class KadSearchInput(TypedDict):
    page: int
    count: int
    courts: Sequence[str]
    side_name: str | None


class KadSearchWire(TypedDict):
    Page: int
    Count: int
    Courts: list[str]
    SideName: str | None


def _kad_search_wire(source: KadSearchInput) -> KadSearchWire:
    return {
        "Page": source["page"],
        "Count": source["count"],
        "Courts": list(source["courts"]),
        "SideName": source["side_name"],
    }


KAD_SEARCH_BODY = BodyProjection(
    KadSearchInput,
    KadSearchWire,
    _kad_search_wire,
    JsonBody(),
)


class AsyncKadApi(AsyncApi):
    @api.post("/Kad/SearchInstances", body=KAD_SEARCH_BODY, response=Json())
    async def search_instances(
        self,
        *,
        page: int = 1,
        count: int = 25,
        courts: Sequence[str] = (),
        side_name: str | None = None,
    ) -> User:
        raise NotImplementedError


class MethodApi(SyncApi):
    @api.head("/resource", response=Json())
    def head_resource(self) -> User:
        raise NotImplementedError

    @api.options("/resource", response=Json())
    def options_resource(self) -> User:
        raise NotImplementedError

    @api.trace("/resource", response=Json())
    def trace_resource(self) -> User:
        raise NotImplementedError

    @api.request("PROPFIND", "/resource", response=Json())
    def propfind_resource(self) -> User:
        raise NotImplementedError


async def _static_authoring_proof(api_instance: AsyncPrepareApi, sdk: StoreSdk) -> None:
    result = await api_instance.create(body={"name": "Ada"})
    prepared = await api_instance.create.prepare(
        body={"name": "Ada"},
        options=PrepareOptions(),
    )
    assert_type(result, User)
    assert_type(prepared, PreparedCall)
    assert_type(sdk.users, RootUsersApi)


def test_singular_response_normalizes_and_inherits_default_errors() -> None:
    descriptor = inspect.getattr_static(ShorthandApi, "get_user")
    declaration = descriptor.resolve(ShorthandApi.defaults)

    assert declaration.result_type is User
    assert len(declaration.responses.success) == 1
    success = declaration.responses.success[0]
    assert success.status == 201
    assert success.condition is _regular_html
    assert isinstance(success.response, Json)
    assert success.response.model is User
    assert declaration.responses.errors == (COMMON_ERROR, LOCAL_ERROR)


def test_singular_response_can_disable_error_inheritance() -> None:
    descriptor = inspect.getattr_static(ShorthandApi, "isolated")
    declaration = descriptor.resolve(ShorthandApi.defaults)
    assert declaration.responses.errors == (LOCAL_ERROR,)


def test_response_and_responses_are_mutually_exclusive() -> None:
    responses: Responses[User] = Responses(success=(Success(200, Json(User)),))
    with pytest.raises(TypeError, match="mutually exclusive"):
        cast(Any, api.get)("/users", response=Json(), responses=responses)


def test_singular_response_requires_an_explicit_return_annotation() -> None:
    with pytest.raises(TypeError, match="requires a return annotation"):

        class InvalidApi(SyncApi):
            @api.get("/invalid", response=Json())
            def invalid(  # type: ignore[no-untyped-def]
                self, *, value: Annotated[str, Path()]
            ):
                raise NotImplementedError


def test_callable_parser_binds_one_model_without_runtime_model_argument() -> None:
    calls: list[ResponseContext[object]] = []

    def parse(context: ResponseContext[object]) -> ParsedValue[User]:
        calls.append(context)
        return ParsedValue(User(cast(dict[str, str], context.json.value)["name"]))

    parser = callable_parser(User, parse)
    parsed_responses: Responses[User] = Responses(
        success=(Success(200, Parsed(User, parser)),),
    )
    context = ResponseContext(
        NormalizedResponse(
            200,
            "https://api.example/users/1",
            "GET",
            {"Content-Type": "application/json"},
            b'{"name":"Ada"}',
        )
    )

    outcome = parsed_responses.inspect(context)
    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value == User("Ada")
    assert calls == [context]


def test_bound_prepare_uses_the_real_pipeline_without_sending() -> None:
    sends = 0

    def emit(*args: object, **kwargs: object) -> object:
        nonlocal sends
        sends += 1
        raise AssertionError("prepare must stop before handler emission")

    client = _SyncClientCore(ExecutionRuntime(CAPABILITIES, emit, "https://api.test"))
    prepared = PrepareApi(client).create.prepare(body={"name": "Ada"})

    assert sends == 0
    assert prepared.method == "POST"
    assert prepared.url == "https://api.test/users?page=1"
    assert prepared.query[0].value == "1"
    assert prepared.cookies[0].name == "session"
    assert prepared.cookies[0].value == "<redacted>"
    assert prepared.body == (("name", "Ada"),)
    assert prepared.encoded_body == b'{"name":"Ada"}'


def test_async_bound_prepare_has_the_same_stop_before_emit_boundary() -> None:
    sends = 0

    async def emit(*args: object, **kwargs: object) -> object:
        nonlocal sends
        sends += 1
        raise AssertionError("prepare must stop before handler emission")

    client = _AsyncClientCore(ExecutionRuntime(CAPABILITIES, emit, "https://api.test"))
    prepared = asyncio.run(
        AsyncPrepareApi(client).create.prepare(body={"name": "Ada"})
    )

    assert sends == 0
    assert prepared.target == "/users?page=1"


def test_pure_prepare_reports_managed_requirements_and_full_mode_redacts_them() -> None:
    scheme = BearerScheme()

    class SecuredApi(SyncApi):
        @api.get("/secured", response=Json(), security=scheme)
        def secured(self) -> User:
            raise NotImplementedError

    providers = AuthProviders()
    providers.register(
        scheme,
        StaticAuthProvider(scheme, "token", AuthProviderIdentity("phase25")),
    )
    runtime = ExecutionRuntime(
        CAPABILITIES,
        lambda *args, **kwargs: None,
        "https://api.test",
        auth=providers,
    )
    operation: Any = SecuredApi(_SyncClientCore(runtime)).secured

    with pytest.raises(PreparationIncomplete) as captured:
        operation.prepare()
    assert captured.value.requirements == ("authentication",)

    prepared = operation.prepare(options=PrepareOptions(resolve_managed=True))
    authorization = next(
        field for field in prepared.headers if field.name.lower() == "authorization"
    )
    assert authorization.value == "<redacted>"


def test_full_prepare_does_not_commit_managed_protection_state() -> None:
    requirement = SolverRequirement[str, str]("phase25.before")

    class Solver:
        calls = 0

        async def solve(self, challenge: str, context: SolveContext) -> str:
            self.calls += 1
            assert challenge == "challenge"
            return "managed-secret"

    solver = Solver()
    policy = before_call_policy(
        identity="phase25.before",
        scope=RequestScope(operation_ids=frozenset({"managed"})),
        challenge="challenge",
        solver=requirement,
        apply=private_bindings(private_header("X-Managed")),
        persistence=until_rejected(),
    )

    class ManagedApi(AsyncApi):
        @api.get("/managed", operation_id="managed", response=Json())
        async def managed(self) -> User:
            raise NotImplementedError

    runtime = ExecutionRuntime(
        CAPABILITIES,
        lambda *args, **kwargs: None,
        "https://api.test",
        before_call_policies=(policy,),
        challenge_solvers=ChallengeSolverBindings(
            bind_challenge_solver(requirement, solver)
        ),
    )
    prepared = asyncio.run(
        ManagedApi(_AsyncClientCore(runtime)).managed.prepare(
            options=PrepareOptions(resolve_managed=True)
        )
    )

    assert solver.calls == 1
    assert runtime._protection_state == {}
    managed = next(field for field in prepared.headers if field.name == "X-Managed")
    assert managed.value == "<redacted>"


def test_sync_root_from_handler_binds_groups_and_owns_resources_exactly_once() -> None:
    handler = RecordingHandler(
        status=200,
        headers={"Content-Type": "application/json"},
        content=b'{"name":"Ada"}',
    )

    with StoreSdk.from_handler(
        handler=handler,
        base_url="https://api.test",
    ) as sdk:
        assert sdk.users is sdk.users
        assert sdk.users.get(user_id=7) == User("Ada")
        handler.assert_request(method="GET", url="https://api.test/users/7")

    assert handler.close_calls == 1
    sdk.close()
    assert handler.close_calls == 1


def test_async_root_from_handler_uses_async_groups_and_ownership() -> None:
    async def run() -> None:
        handler = AsyncRecordingHandler(
            status=200,
            headers={"Content-Type": "application/json"},
            content=b'{"name":"Grace"}',
        )
        async with AsyncStoreSdk.from_handler(
            handler=handler,
            base_url="https://api.test",
        ) as sdk:
            assert await sdk.users.get(user_id=8) == User("Grace")
            handler.assert_request(method="GET", url="https://api.test/users/8")
        assert handler.close_calls == 1

    asyncio.run(run())


def test_root_from_borrowed_client_leaves_client_ownership_with_caller() -> None:
    handler = RecordingHandler(
        status=200,
        headers={"Content-Type": "application/json"},
        content=b'{"name":"Lin"}',
    )
    client = Client(base_url="https://api.test", handler=handler)
    sdk = StoreSdk.from_client(client)

    sdk.close()
    assert handler.close_calls == 0
    client.close()
    assert handler.close_calls == 1


def test_root_rejects_a_group_with_the_wrong_execution_kind() -> None:
    with pytest.raises(TypeError, match="wrong API kind"):

        class InvalidRoot(SyncSdk):
            users = api_group(AsyncRootUsersApi)


def test_standard_and_arbitrary_http_methods_share_one_decorator_path() -> None:
    expected = {
        "head_resource": "HEAD",
        "options_resource": "OPTIONS",
        "trace_resource": "TRACE",
        "propfind_resource": "PROPFIND",
    }
    assert {
        name: inspect.getattr_static(MethodApi, name).declaration.method
        for name in expected
    } == expected

    with pytest.raises(ValueError, match="invalid HTTP method token"):
        api.request("BAD METHOD", "/resource", response=Json())


def test_kad_shaped_direct_signature_projects_defaults_without_forwarding_wrapper() -> None:
    client = _AsyncClientCore(
        ExecutionRuntime(CAPABILITIES, lambda *args, **kwargs: None, "https://kad.test")
    )
    operation = AsyncKadApi(client).search_instances
    signature = inspect.signature(operation)

    assert tuple(signature.parameters) == ("page", "count", "courts", "side_name")
    assert tuple(item.default for item in signature.parameters.values()) == (
        1,
        25,
        (),
        None,
    )
    assert all(
        item.kind is inspect.Parameter.KEYWORD_ONLY
        for item in signature.parameters.values()
    )

    prepared = asyncio.run(operation.prepare())
    assert prepared.body == (
        ("Page", 1),
        ("Count", 25),
        ("Courts", ()),
        ("SideName", None),
    )
    assert not hasattr(AsyncKadApi, "_search_instances")
