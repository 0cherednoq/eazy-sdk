from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from typing import Annotated, Any, cast

import pytest

from eazy_sdk import ApiDefaults, AsyncApi, SyncApi, api
from eazy_sdk.auth import BearerScheme
from eazy_sdk.clients import (
    CallOptions,
)
from eazy_sdk.clients.async_client import _AsyncClientCore
from eazy_sdk.clients.sync_client import _SyncClientCore
from eazy_sdk.dependencies import dependency, field
from eazy_sdk.ext import (
    AuthProviderIdentity,
    AuthProviders,
    ExecutionRuntime,
    HttpProtocol,
    ParsedValue,
    PreparedRequest,
    ReplayableBodyStream,
    StaticAuthProvider,
    callable_parser,
)
from eazy_sdk.handlers import (
    AutomaticHeaderPolicy,
    CapabilityLevel,
    EmitOptions,
    HandlerProfile,
    RedirectControl,
    TransportFailure,
)
from eazy_sdk.middleware import (
    AttemptRequestContext,
    CallMiddlewareContext,
    MiddlewareProtocolError,
    ProposeAction,
    ReplaceResponse,
    RetryAttempt,
    attempt_middleware,
    call_middleware,
)
from eazy_sdk.protection import (
    BeforeCallPolicy,
    ChallengeSolverBindings,
    ResponseSignal,
    SolveContext,
    SolverRequirement,
    before_call_policy,
    bind_challenge_solver,
    challenge_policy,
    per_call,
    per_match,
    private_bindings,
    private_cookie,
    private_header,
    safe_method,
)
from eazy_sdk.ratelimit_runtime import RateLimitContext, RateLimitDecision
from eazy_sdk.request import (
    Header,
    Query,
    ReplayableStreamBody,
    SigningKey,
    SigningKeyRequirement,
    WireOptions,
    header_output,
    hmac_sha256,
    method,
)
from eazy_sdk.response import Error, Headers, Json, NormalizedResponse, Responses, Success, Text

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


class ItemsApi(SyncApi):
    @api.get(
        "/items",
        operation_id="items.get",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def items(self, *, page: Annotated[int | None, Query("page")] = None) -> dict[str, object]:
        raise NotImplementedError


class AsyncItemsApi(AsyncApi):
    @api.get(
        "/items",
        operation_id="items.get",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def items(
        self, *, page: Annotated[int | None, Query("page")] = None
    ) -> dict[str, object]:
        raise NotImplementedError


class DeviceItemsApi(SyncApi):
    @api.get(
        "/items",
        operation_id="items.get",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def items(
        self,
        *,
        page: Annotated[int | None, Query("page")] = None,
        device: Annotated[str | None, Header("X-Device")] = None,
    ) -> dict[str, object]:
        raise NotImplementedError


class StreamApi(SyncApi):
    @api.post(
        "/stream",
        operation_id="stream.upload",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def upload(
        self,
        *,
        body: Annotated[ReplayableBodyStream, ReplayableStreamBody()],
    ) -> dict[str, object]:
        raise NotImplementedError


def response(
    body: bytes = b'{"ok":true}', *, status: int = 200, headers: Any = None
) -> NormalizedResponse[object]:
    return NormalizedResponse(
        status,
        "https://api.test/items",
        "GET",
        Headers(headers or (("content-type", "application/json"),)),
        body,
    )


def contract() -> Any:
    return cast(Any, ItemsApi.items).resolve(ApiDefaults())


def execute_sync(
    client: _SyncClientCore[Any],
    declaration: Any,
    values: dict[str, object] | None = None,
    *,
    options: CallOptions | None = None,
) -> Any:
    return client._execute_operation(
        declaration,
        values or {},
        options=options,
        with_response=False,
    )


async def execute_async(
    client: _AsyncClientCore[Any],
    declaration: Any,
    values: dict[str, object] | None = None,
    *,
    options: CallOptions | None = None,
) -> Any:
    return await client._execute_operation(
        declaration,
        values or {},
        options=options,
        with_response=False,
    )


def test_sync_and_async_clients_use_the_same_core() -> None:
    sync_trace: list[str] = []
    async_trace: list[str] = []

    def sync_emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        assert request.target == b"/items?page=2"
        return response()

    async def async_emit(
        request: PreparedRequest, *, options: EmitOptions
    ) -> NormalizedResponse[object]:
        assert request.target == b"/items?page=2"
        return response()

    def sync_observer(phase: str, value: object | None) -> None:
        sync_trace.append(phase)

    def async_observer(phase: str, value: object | None) -> None:
        async_trace.append(phase)

    sync_client = _SyncClientCore(
        ExecutionRuntime(CAPABILITIES, sync_emit, "https://api.test", observer=sync_observer)
    )
    async_client = _AsyncClientCore(
        ExecutionRuntime(CAPABILITIES, async_emit, "https://api.test", observer=async_observer)
    )
    sync_value = ItemsApi(sync_client).items(page=2)
    async_value = asyncio.run(AsyncItemsApi(async_client).items(page=2))
    assert sync_value == async_value == {"ok": True}
    assert sync_trace == async_trace == ["start_attempt", "prepared", "emit"]


def test_generic_request_passes_through_the_same_executor() -> None:
    seen: list[PreparedRequest] = []

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        seen.append(request)
        return response(b"raw", headers=(("content-type", "text/plain"),))

    client = _SyncClientCore(ExecutionRuntime(CAPABILITIES, emit))
    result = client.get("https://api.test/raw", params={"x": "a b"})
    assert result.body == b"raw"
    assert seen[0].target == b"/raw?x=a%20b"


def test_preflight_fails_before_attempt_middleware() -> None:
    called = False

    @dataclass
    class Contributor:
        def contribute(self, context: AttemptRequestContext) -> None:
            nonlocal called
            called = True

    limited = dataclass_replace(CAPABILITIES, exact_target=CapabilityLevel.BEST_EFFORT)
    client = _SyncClientCore(
        ExecutionRuntime(
            limited,
            lambda *_args, **_kwargs: response(),
            middleware=(attempt_middleware(Contributor()),),
        )
    )
    with pytest.raises(Exception, match="capability mismatch"):
        endpoint = dataclass_replace(contract(), wire=WireOptions(exact=True))
        execute_sync(client, endpoint)
    assert not called


def test_stream_capability_fails_before_transport_for_unsupported_handler() -> None:
    called = False

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal called
        called = True
        return response()

    unsupported = dataclass_replace(CAPABILITIES, replayable_streams=CapabilityLevel.UNSUPPORTED)
    client = _SyncClientCore(ExecutionRuntime(unsupported, emit, "https://api.test"))

    with pytest.raises(Exception, match="replayable_streams"):
        StreamApi(client).upload(
            body=ReplayableBodyStream(lambda: BytesIO(b"stream"), known_length=6)
        )

    assert not called


def test_transport_retry_restarts_attempt_and_reserves_limiter() -> None:
    attempts = 0
    reservations: list[RateLimitContext] = []

    class Limiter:
        def reserve(self, context: RateLimitContext) -> RateLimitDecision:
            reservations.append(context)
            return RateLimitDecision()

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TransportFailure("fake", "emit", 1, OSError("gone"))
        return response()

    client = _SyncClientCore(
        ExecutionRuntime(CAPABILITIES, emit, "https://api.test", limiter=Limiter())
    )
    assert execute_sync(
        client, contract(), options=CallOptions(max_attempts=2, transport_retries=1)
    ) == {"ok": True}
    assert [item.attempt_kind for item in reservations] == ["initial", "transport-retry"]


def test_redirect_restarts_preparation_and_recomputes_scope() -> None:
    targets: list[str] = []
    contributions: list[str] = []
    signatures: list[bytes] = []
    key_calls = 0

    @dataclass
    class Scoped:
        label: str

        def contribute(self, context: AttemptRequestContext) -> None:
            contributions.append(self.label)

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        targets.append(request.url)
        signatures.append(
            next(field.value for field in request.headers if field.name == b"X-Signature")
        )
        if len(targets) == 1:
            return response(
                b"",
                status=302,
                headers=(("location", "https://other.test/final"),),
            )
        return response()

    from eazy_sdk._internal import RequestScope

    def key_provider(_requirement: SigningKeyRequirement) -> SigningKey:
        nonlocal key_calls
        key_calls += 1
        return SigningKey(f"redirect-{key_calls}".encode())

    signature = hmac_sha256(
        key=SigningKeyRequirement("redirect-key"),
        base=method(),
        output=header_output("X-Signature"),
    )

    runtime = ExecutionRuntime(
        CAPABILITIES,
        emit,
        "https://api.test",
        middleware=(
            attempt_middleware(Scoped("api"), scope=RequestScope(hosts=frozenset({"api.test"}))),
            attempt_middleware(
                Scoped("other"), scope=RequestScope(hosts=frozenset({"other.test"}))
            ),
        ),
        key_provider=key_provider,
    )
    signed = dataclass_replace(contract(), signing=(signature,))
    value = execute_sync(
        _SyncClientCore(runtime),
        signed,
        options=CallOptions(max_attempts=2, max_redirects=1),
    )
    assert value == {"ok": True}
    assert targets == ["https://api.test/items", "https://other.test/final"]
    assert contributions == ["api", "other"]
    assert key_calls == 2
    assert len(set(signatures)) == 2


def test_response_reaction_rebuilds_and_resigns_the_request() -> None:
    from eazy_sdk._internal import RequestScope

    @dataclass(frozen=True)
    class Challenge:
        token: str

    class Solver:
        async def solve(self, challenge: Challenge, context: SolveContext) -> str:
            assert challenge.token == "challenge-1"
            assert context.attempt == 1
            return "solved"

    challenge_case = Error(409, Json(Challenge))
    responses: Responses[dict[str, object]] = Responses(
        success=(Success(200, Json(dict)),),
        errors=(challenge_case,),
    )
    requirement: SolverRequirement[Challenge, str] = SolverRequirement("challenge")
    scope = RequestScope()

    def parse_challenge(context: Any) -> Any:
        value = context.json.value
        return ParsedValue(Challenge(value["token"]))

    signal = ResponseSignal(
        "challenge",
        scope,
        Challenge,
        callable_parser(Challenge, parse_challenge),
        prefilter=lambda context: context.response.status_code == 409,
    )
    policy = challenge_policy(
        scope=scope,
        signal=signal,
        solver=requirement,
        apply=private_bindings(private_header("X-Device")),
        persistence=per_match(),
        replay=safe_method(),
    )

    signatures: list[bytes] = []
    requests: list[PreparedRequest] = []
    key_calls = 0

    def key_provider(_requirement: SigningKeyRequirement) -> SigningKey:
        nonlocal key_calls
        key_calls += 1
        return SigningKey(f"reaction-{key_calls}".encode())

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        requests.append(request)
        signatures.append(
            next(field.value for field in request.headers if field.name == b"X-Signature")
        )
        if len(requests) == 1:
            return response(b'{"token":"challenge-1"}', status=409)
        assert any(
            field.name == b"X-Device" and field.value == b"solved" for field in request.headers
        )
        return response()

    signature = hmac_sha256(
        key=SigningKeyRequirement("reaction-key"),
        base=method(),
        output=header_output("X-Signature"),
    )
    declaration = dataclass_replace(
        cast(Any, ItemsApi.items).resolve(ApiDefaults()),
        responses=responses,
        signing=(signature,),
    )
    runtime = ExecutionRuntime(
        CAPABILITIES,
        emit,
        "https://api.test",
        challenge_policies=(policy,),
        challenge_solvers=ChallengeSolverBindings(
            bind_challenge_solver(requirement, Solver())
        ),
        key_provider=key_provider,
    )

    assert execute_sync(_SyncClientCore(runtime), declaration) == {"ok": True}
    assert len(requests) == 2
    assert requests[0] is not requests[1]
    assert key_calls == 2
    assert len(set(signatures)) == 2


def test_attempt_middleware_can_replace_response_before_cases() -> None:
    class Decoder:
        def after_response(self, context: Any) -> ReplaceResponse:
            return ReplaceResponse(response())

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        return response(b"encrypted", headers=(("content-type", "application/octet-stream"),))

    runtime = ExecutionRuntime(
        CAPABILITIES, emit, "https://api.test", middleware=(attempt_middleware(Decoder()),)
    )
    assert execute_sync(_SyncClientCore(runtime), contract()) == {"ok": True}


def test_middleware_action_reenters_start_attempt() -> None:
    emits = 0

    class ReplayOnce:
        def after_response(self, context: Any) -> ProposeAction | None:
            return ProposeAction(RetryAttempt()) if context.attempt == 1 else None

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal emits
        emits += 1
        return response()

    runtime = ExecutionRuntime(
        CAPABILITIES, emit, "https://api.test", middleware=(attempt_middleware(ReplayOnce()),)
    )
    assert execute_sync(
        _SyncClientCore(runtime), contract(), options=CallOptions(max_attempts=2)
    ) == {"ok": True}
    assert emits == 2


def test_client_before_policy_uses_nested_operation_and_rebuilds_request() -> None:
    from eazy_sdk._internal import RequestScope

    class AcquireApi(SyncApi):
        @api.get(
            "/token",
            operation_id="token.acquire",
            responses=Responses(success=(Success(200, Text()),)),
        )
        def token(self) -> str:
            raise NotImplementedError

    class ProtectedApi(SyncApi):
        @api.get(
            "/protected",
            operation_id="protected",
            responses=Responses(success=(Success(200, Json(dict)),)),
        )
        def protected(self) -> dict[str, object]:
            raise NotImplementedError

    policy: BeforeCallPolicy[Any, Any] = before_call_policy(
        identity="test.acquire-clearance",
        scope=RequestScope(operation_ids=frozenset({"protected"})),
        acquire=AcquireApi.token,
        apply=private_bindings(private_cookie("clearance")),
        persistence=per_call(),
    )
    seen: list[str] = []

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        seen.append(request.url)
        if request.target == b"/token":
            return response(b"token", headers=(("content-type", "text/plain"),))
        cookie = next(field.value for field in request.headers if field.name.lower() == b"cookie")
        assert cookie == b"clearance=token"
        return response()

    client = _SyncClientCore(
        ExecutionRuntime(
            CAPABILITIES,
            emit,
            "https://api.test",
            before_call_policies=(policy,),
        )
    )
    assert ProtectedApi(client).protected() == {"ok": True}
    assert seen == ["https://api.test/token", "https://api.test/protected"]


def test_auth_rate_limit_signing_and_emit_have_fixed_order() -> None:
    events: list[str] = []
    bearer = BearerScheme()

    class Provider:
        async def resolve(self) -> Any:
            events.append("auth")
            return await StaticAuthProvider(bearer, "token", AuthProviderIdentity("test")).resolve()

    class Limiter:
        def reserve(self, context: RateLimitContext) -> RateLimitDecision:
            events.append("rate")
            return RateLimitDecision()

    key = SigningKeyRequirement("key")
    signed = dataclass_replace(
        contract(),
        security=bearer,
        signing=(hmac_sha256(key=key, base=method(), output=header_output("X-Signature")),),
    )
    providers = AuthProviders()
    providers.register(bearer, Provider())

    def key_provider(requirement: SigningKeyRequirement) -> SigningKey:
        events.append("sign")
        return SigningKey(b"secret")

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        events.append("emit")
        values = {item.name.lower(): item.value for item in request.headers}
        assert values[b"authorization"] == b"Bearer token"
        assert b"x-signature" in values
        return response()

    runtime = ExecutionRuntime(
        CAPABILITIES,
        emit,
        "https://api.test",
        auth=providers,
        limiter=Limiter(),
        key_provider=key_provider,
    )
    assert execute_sync(_SyncClientCore(runtime), signed) == {"ok": True}
    assert events == ["auth", "rate", "sign", "emit"]


def test_high_level_dependency_is_lowered_and_cached_per_call() -> None:
    @dataclass(frozen=True)
    class Device:
        identifier: str

    calls = 0

    def provide(context: Any) -> Device:
        nonlocal calls
        calls += 1
        return Device("device-1")

    requirement = dependency(
        Device,
        provide=provide,
        apply=(field("identifier").to_header("X-Device"),),
    )
    endpoint = dataclass_replace(
        cast(Any, DeviceItemsApi.items).resolve(ApiDefaults()),
        requires=(requirement,),
    )
    emits = 0

    def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        nonlocal emits
        emits += 1
        assert any(
            item.name == b"X-Device" and item.value == b"device-1" for item in request.headers
        )
        if emits == 1:
            raise TransportFailure("fake", "emit", 1, OSError("retry"))
        return response()

    client = _SyncClientCore(ExecutionRuntime(CAPABILITIES, emit, "https://api.test"))
    assert execute_sync(
        client, endpoint, options=CallOptions(max_attempts=2, transport_retries=1)
    ) == {"ok": True}
    assert calls == 1


async def test_call_middleware_is_onion_and_next_is_single_use() -> None:
    events: list[str] = []

    class Outer:
        async def __call__(self, context: CallMiddlewareContext[Any], call_next: Any) -> Any:
            events.append("outer-in")
            result = await call_next(context)
            events.append("outer-out")
            with pytest.raises(MiddlewareProtocolError):
                await call_next(context)
            return result

    class Inner:
        async def __call__(self, context: CallMiddlewareContext[Any], call_next: Any) -> Any:
            events.append("inner-in")
            result = await call_next(context)
            events.append("inner-out")
            return result

    async def emit(request: PreparedRequest, *, options: EmitOptions) -> NormalizedResponse[object]:
        return response()

    runtime = ExecutionRuntime(
        CAPABILITIES,
        emit,
        "https://api.test",
        middleware=(call_middleware(Outer()), call_middleware(Inner())),
    )
    assert await execute_async(_AsyncClientCore(runtime), contract()) == {"ok": True}
    assert events == ["outer-in", "inner-in", "inner-out", "outer-out"]


def dataclass_replace(value: Any, **changes: Any) -> Any:
    from dataclasses import replace

    return replace(value, **changes)
