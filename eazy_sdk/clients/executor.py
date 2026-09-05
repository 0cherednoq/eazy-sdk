"""The single logical-call/attempt coordinator used by both public clients."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, cast, get_type_hints
from urllib.parse import urljoin, urlsplit

from eazy_sdk.auth.core import (
    AuthProviders,
    _has_refreshable_security,
    _refresh_security,
    resolve_security,
)
from eazy_sdk.auth.lifecycle import LifecycleGraph
from eazy_sdk.compile import (
    compile_endpoint,
)
from eazy_sdk.compile.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk.core import (
    Bind,
    BindingError,
    BoundArguments,
    OperationBindingError,
    OperationIdentity,
    OperationValues,
    PlanError,
    ScopeContext,
    ValuePatch,
    WireRequirement,
    WireRequirements,
    apply_patch_atomic,
    bind_plan,
)
from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoDirection,
    CryptoInputScope,
    CryptoOutputValue,
    CryptoRegistry,
    CryptoStage,
    CryptoStreamingUnsupportedError,
    CryptoValues,
    FrozenValue,
    HttpCryptoContext,
    HttpEncrypted,
    PayloadCrypto,
)
from eazy_sdk.crypto._http import (
    prepare_http_document,
    protect_http_request,
    unprotect_http_response,
)
from eazy_sdk.crypto._inputs import resolve_crypto_inputs
from eazy_sdk.crypto._runtime import (
    CompiledPayloadCrypto,
    compile_payload_crypto,
    validate_crypto_runtime,
)
from eazy_sdk.dependencies import (
    DependencyRegistry,
    _DependencyCaches,
    _lower_requirements,
    _resolve_requirements,
)
from eazy_sdk.handlers import EmitOptions, HandlerProfile, TransportError, validate_profile
from eazy_sdk.middleware import (
    AttemptMiddlewareRegistration,
    AttemptRequestContext,
    AttemptResponseContext,
    AttemptTransportErrorContext,
    CallMiddlewareContext,
    CallMiddlewareRegistration,
    Fail,
    PreparedAttemptContext,
    ProposeAction,
    ReplaceResponse,
    SingleUseNext,
)
from eazy_sdk.models import (
    ModelAdapterRegistry,
    default_model_adapters,
)
from eazy_sdk.preparation import PreparationIncompleteError, PreparedCall, PrepareOptions
from eazy_sdk.protection.advanced import (
    AmbiguousChallengeError,
    AmbiguousSignal,
    BeforeCallPolicy,
    ChallengeApplicationError,
    ChallengeParseError,
    ChallengePolicy,
    ChallengeSolveError,
    ChallengeSolver,
    InstallableProtection,
    MalformedSignal,
    MissingSolverError,
    PrivateBindings,
    ProtectedFetch,
    ProtectionConfigurationError,
    ProtectionFlow,
    ProtectionPersistence,
    ProtectionPersistenceMode,
    ProtectionStateScope,
    SolveContext,
    SolverBindings,
    TransportIdentity,
    _ensure_replay_allowed,
    _inspect_signals,
    _private_bindings_patch,
)
from eazy_sdk.ratelimit_runtime import RateLimitContext, RateLimiter
from eazy_sdk.request import (
    JsonBody,
    ReplayableStreamBody,
    SigningKey,
    SigningKeyRequirement,
    WireProfile,
)
from eazy_sdk.request.logical import ExactBodyInput, NoBodyInput
from eazy_sdk.request.prepared import (
    _NO_BODY_DOCUMENT_OVERRIDE,
    BufferedBody,
    HeaderField,
    HttpProtocol,
    PreparedRequest,
    RequestPreparer,
)
from eazy_sdk.request.signatures import reserve_outputs, sign_prepared
from eazy_sdk.response import (
    NormalizedResponse,
    ResponseContext,
    ResponseEnvelope,
    Responses,
)
from eazy_sdk.response.cases import AttemptIdentity, OperationInfo, PreparedRequestSummary

from ._http_stages import (
    AuthRefreshTransition,
    ReactionTransition,
    RedirectTransition,
    RejectedResponse,
    RequestDocumentStageInput,
    ResponseDecisionInput,
    RetryTransition,
    TerminalResponse,
    build_request_document,
    decide_response,
)

type KeyProvider = Callable[[SigningKeyRequirement], SigningKey]
type Observer = Callable[[str, object | None], None]


_operation_stack: ContextVar[tuple[str, ...]] = ContextVar("eazy_sdk_operation_stack", default=())


@dataclass(frozen=True, slots=True)
class ExecutionResult[T]:
    value: T
    response: NormalizedResponse[Any]


@dataclass(frozen=True, slots=True)
class _MandatoryPreparation:
    flows: tuple[
        tuple[
            ProtectionFlow[Any],
            _OperationDeclaration[Any],
            _OperationDeclaration[Any] | None,
        ],
        ...,
    ]
    writers: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _CompiledChallengePolicy:
    identity: str
    revision: int
    signal: Any
    solver: Any
    apply: PrivateBindings[Any]
    persistence: ProtectionPersistence
    replay: Any
    challenge_identity: Callable[[Any], object] | None


@dataclass(frozen=True, slots=True)
class _CompiledBeforeCallPolicy:
    identity: str
    revision: int
    acquire: _OperationDeclaration[Any] | None
    challenge: object | None
    solver: Any | None
    apply: PrivateBindings[Any]
    persistence: ProtectionPersistence


@dataclass(frozen=True, slots=True)
class _ManagedProtectionState:
    solution: object
    generation: int
    identity: str | None = None
    """``TransportIdentity.fingerprint()`` of the session that acquired ``solution``."""

    def __repr__(self) -> str:
        return (
            "_ManagedProtectionState(solution=<redacted>, "
            f"generation={self.generation}, identity={self.identity!r})"
        )


@dataclass(frozen=True, slots=True)
class _RuntimeFetch:
    """``ProtectedFetch`` over the runtime's own handler, proxy and cookie jar.

    Requests are emitted directly through ``runtime.send``: no guard, reaction,
    replay, middleware or rate limiter runs, so a solver cannot recurse into the
    protection pipeline that invoked it.
    """

    runtime: ExecutionRuntime
    options: EmitOptions
    identity: TransportIdentity

    async def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> NormalizedResponse[object]:
        prepared = _raw_prepared_request(
            url,
            method,
            headers,
            body,
            user_agent=self.identity.user_agent,
            protocol=(
                self.runtime.profile.protocol
                if self.runtime.profile is not None
                else HttpProtocol.HTTP_1_1
            ),
        )
        response = await _maybe_await(self.runtime.send(prepared, options=self.options))
        if not isinstance(response, NormalizedResponse):
            raise TypeError("transport returned a non-normalized response to a solver fetch")
        return cast(NormalizedResponse[object], response)


def _raw_prepared_request(
    url: str,
    method: str,
    headers: Mapping[str, str] | None,
    body: bytes | None,
    *,
    user_agent: str | None,
    protocol: HttpProtocol,
) -> PreparedRequest:
    split = urlsplit(url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        raise ValueError("solver fetch requires an absolute http(s) URL")
    if not method or not method.isascii() or any(c.isspace() for c in method):
        raise ValueError("solver fetch method must be an ASCII token")
    target = (split.path or "/") + (f"?{split.query}" if split.query else "")
    fields: list[HeaderField] = []
    names: set[bytes] = set()
    for name, value in (headers or {}).items():
        if not name or any(c in name for c in "\r\n\x00: "):
            raise ValueError(f"solver fetch header name is invalid: {name!r}")
        if any(c in value for c in "\r\n\x00"):
            raise ValueError(f"solver fetch header {name!r} has an invalid value")
        fields.append(HeaderField(name.encode("ascii"), value.encode("utf-8")))
        names.add(name.encode("ascii").lower())
    if user_agent is not None and b"user-agent" not in names:
        fields.append(HeaderField(b"User-Agent", user_agent.encode("utf-8")))
    if b"host" not in names:
        fields.append(HeaderField(b"Host", split.netloc.encode("ascii")))
    content_type = next(
        (field.value for field in fields if field.name.lower() == b"content-type"),
        None,
    )
    if body is not None and b"content-length" not in names:
        fields.append(HeaderField(b"Content-Length", str(len(body)).encode("ascii")))
    media_type = content_type.decode("ascii") if content_type is not None else None
    return PreparedRequest(
        method=method.upper().encode("ascii"),
        scheme=split.scheme.encode("ascii"),
        authority=split.netloc.encode("ascii"),
        target=target.encode("utf-8"),
        headers=tuple(fields),
        body=BufferedBody(body or b"", content_type),
        protocol=protocol,
        body_input=ExactBodyInput(body, media_type) if body is not None else NoBodyInput(),
    )


def _transport_identity(
    runtime: ExecutionRuntime,
    headers: Mapping[str, str],
) -> TransportIdentity:
    """Identity of one attempt: public header values plus what the handler declares.

    ``headers`` are the values bound for the attempt *before* managed protection state is
    applied, so a guard that writes ``User-Agent`` cannot change the identity its own
    solution is checked against. Proxy and impersonation come from ``HandlerProfile``.
    """

    user_agent = next(
        (value for name, value in headers.items() if name.lower() == "user-agent"),
        None,
    )
    return TransportIdentity(
        user_agent=user_agent,
        proxy=runtime.handler_profile.proxy,
        impersonation=runtime.handler_profile.impersonation,
    )


def _slot_headers(compiled: Any, values: OperationValues) -> Mapping[str, str]:
    """Header values already bound for this attempt, before request preparation."""

    output: dict[str, str] = {}
    for name, slot in compiled.header_slots.items():
        if not values.contains(slot):
            continue
        value = values.require(slot)
        if isinstance(value, tuple):
            output[name] = ", ".join(str(item) for item in value)
        elif value is not None:
            output[name] = str(value)
    return MappingProxyType(output)


def _prepared_headers(prepared: PreparedRequest) -> Mapping[str, str]:
    return MappingProxyType(
        {
            field.name.decode("ascii"): field.value.decode("utf-8", errors="replace")
            for field in prepared.headers
        }
    )


def _solve_deadline(options: Any) -> datetime | None:
    timeout = getattr(options, "timeout", None)
    if timeout is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=float(timeout))


def _solve_context(
    runtime: ExecutionRuntime,
    options: Any,
    operation: OperationIdentity,
    response: ResponseContext[object] | None,
    attempt: int,
    identity: TransportIdentity,
    headers: Mapping[str, str],
) -> SolveContext:
    emit_options = options.emit_options()
    fetch: ProtectedFetch = _RuntimeFetch(runtime, emit_options, identity)
    return SolveContext(
        operation,
        response,
        attempt,
        deadline=_solve_deadline(options),
        fetch=fetch,
        identity=identity,
        request_headers=headers,
    )


type _ProtectionCacheKey = tuple[object, ...]

_MAX_POLL_DELAY = 0.01


@dataclass(slots=True)
class _ProtectionLockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


@dataclass(slots=True)
class _ProtectionLockRegistry:
    """Single-flight registry that is safe across event loops and threads.

    One runtime may be driven by several event loops (a sync client runs one loop per
    thread), so entries use a ``threading.Lock`` instead of a loop-bound ``asyncio.Lock``.
    The lock is acquired without blocking the loop: waiters poll with an exponential
    backoff capped at ``_MAX_POLL_DELAY``, which bounds the extra latency a queued solve
    pays after the in-flight one finishes.
    """

    _entries: dict[_ProtectionCacheKey, _ProtectionLockEntry] = field(
        default_factory=dict
    )
    _guard: threading.Lock = field(default_factory=threading.Lock)

    @asynccontextmanager
    async def hold(self, key: _ProtectionCacheKey) -> AsyncIterator[None]:
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = _ProtectionLockEntry()
                self._entries[key] = entry
            entry.users += 1
        try:
            delay = 0.001
            while not entry.lock.acquire(blocking=False):
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_POLL_DELAY)
            try:
                yield
            finally:
                entry.lock.release()
        finally:
            with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(key) is entry:
                    self._entries.pop(key)

    def __len__(self) -> int:
        with self._guard:
            return len(self._entries)


@dataclass(slots=True)
class ExecutionRuntime:
    handler_profile: HandlerProfile
    send: Any
    base_url: str = ""
    dependencies: DependencyRegistry = field(default_factory=DependencyRegistry)
    auth: AuthProviders = field(default_factory=AuthProviders)
    operation_protections: tuple[ProtectionFlow[Any], ...] = ()
    before_call_policies: tuple[BeforeCallPolicy[Any, Any], ...] = ()
    challenge_policies: tuple[ChallengePolicy[Any, Any], ...] = ()
    solver_bindings: SolverBindings = field(default_factory=SolverBindings)
    protection_session_owner: object | None = None
    middleware: tuple[object, ...] = ()
    limiter: RateLimiter | None = None
    key_provider: KeyProvider | None = None
    observer: Observer | None = None
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters)
    profile: WireProfile | None = None
    crypto: CryptoRegistry = field(default_factory=CryptoRegistry)
    allow_async_crypto: bool = True
    _protection_state: dict[_ProtectionCacheKey, _ManagedProtectionState] = field(
        default_factory=dict, init=False, repr=False
    )
    _protection_locks: _ProtectionLockRegistry = field(
        default_factory=_ProtectionLockRegistry, init=False, repr=False
    )
    _protection_generation: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if any(not isinstance(item, ProtectionFlow) for item in self.operation_protections):
            raise TypeError("operation_protections accepts only ProtectionFlow values")
        if any(not isinstance(item, BeforeCallPolicy) for item in self.before_call_policies):
            raise TypeError("before_call_policies contains a malformed policy")
        if any(not isinstance(item, ChallengePolicy) for item in self.challenge_policies):
            raise TypeError("challenge_policies contains a malformed policy")

    def close_protection_session(self) -> None:
        """Release managed solutions at the owning client/handler lifecycle boundary."""

        self._protection_state.clear()
        self._protection_locks._entries.clear()

    def invalidate_protection(self, identities: Collection[str] | None = None) -> int:
        """Drop cached solutions of the given policy identities (all when ``None``)."""

        removed = 0
        for key in tuple(self._protection_state):
            if identities is None or key[0] in identities:
                self._protection_state.pop(key, None)
                removed += 1
        return removed


class _PreparedRequestCaptured(Exception):
    def __init__(self, request: Any) -> None:
        self.request = request
        super().__init__("request preparation reached the emission boundary")


def _managed_preparation_requirements(
    contract: _OperationDeclaration[Any],
    runtime: ExecutionRuntime,
    options: Any,
) -> tuple[str, ...]:
    scope = _scope_context(contract, runtime.base_url)
    requirements: list[str] = []
    if contract.requires or contract.inject:
        requirements.append("dependencies")
    if contract.security is not None:
        requirements.append("authentication")
    if contract.signing:
        requirements.append("signing keys")
    if contract.crypto is not None or runtime.crypto.rules:
        requirements.append("payload crypto")
    if contract.protections or runtime.operation_protections:
        requirements.append("operation protection")
    if any(policy.scope.matches(scope) for policy in runtime.before_call_policies):
        requirements.append("before-call protection")
    if runtime.limiter is not None:
        requirements.append("rate limiter")
    if runtime.middleware or getattr(options, "middleware", ()):
        requirements.append("middleware")
    return tuple(dict.fromkeys(requirements))


def wire_requirements(contract: _OperationDeclaration[Any]) -> WireRequirements:
    dimensions: list[WireRequirement] = []
    wire = contract.wire
    if wire is not None and wire.protocol is not None:
        dimensions.append(WireRequirement("protocol", wire.protocol))
    if wire is not None and wire.exact:
        dimensions.extend(
            WireRequirement(name, "CAPTURE_VERIFIED")
            for name in (
                "exact_target",
                "header_order",
                "header_casing",
                "preencoded_body",
                "manual_cookie_field",
            )
        )
    if any(isinstance(field.placement, ReplayableStreamBody) for field in contract.input_fields):
        dimensions.append(WireRequirement("replayable_streams", "BEST_EFFORT"))
    return WireRequirements(tuple(dimensions))


class ExecutionCore:
    """Owns ordering, arbitration and all attempt budgets."""

    def __init__(
        self,
        runtime: ExecutionRuntime,
        *,
        resolution_graph: LifecycleGraph | None = None,
    ) -> None:
        self.runtime = runtime
        self.resolution_graph = resolution_graph

    async def prepare[T](
        self,
        call: _OperationCall[T],
        *,
        options: PrepareOptions,
    ) -> PreparedCall:
        """Run the normal pipeline and stop at its handler-emission boundary."""

        call_options = options.call_options
        if call_options is None:
            from eazy_sdk.policies import CallOptions

            call_options = CallOptions()
        runtime = self.runtime
        if not options.resolve_managed:
            requirements = _managed_preparation_requirements(
                call.declaration,
                runtime,
                call_options,
            )
            if requirements:
                raise PreparationIncompleteError(requirements)
            runtime = replace(
                runtime,
                dependencies=DependencyRegistry(),
                auth=AuthProviders(),
                operation_protections=(),
                before_call_policies=(),
                challenge_policies=(),
                solver_bindings=SolverBindings(),
                middleware=(),
                limiter=None,
                key_provider=None,
                crypto=CryptoRegistry(),
                observer=None,
            )

        def stop(request: object, *, options: object) -> None:
            from eazy_sdk.request.prepared import PreparedRequest

            if not isinstance(request, PreparedRequest):
                raise TypeError("preparation boundary received an invalid request")
            raise _PreparedRequestCaptured(request)

        runtime = replace(runtime, send=stop, observer=None)
        core = ExecutionCore(runtime, resolution_graph=self.resolution_graph)
        try:
            await core.execute(call, options=call_options)
        except _PreparedRequestCaptured as captured:
            return PreparedCall._from_request(captured.request)
        raise PreparationIncompleteError(("pipeline did not reach request emission",))

    async def execute[T](
        self,
        call: _OperationCall[T],
        *,
        options: object,
    ) -> ExecutionResult[T]:
        from eazy_sdk.policies import CallOptions

        selected = cast(CallOptions, options)
        contract = call.declaration
        arguments = call.arguments
        initial_url = _contract_url(self.runtime.base_url, contract.path)
        initial_crypto = _resolve_http_crypto(contract, self.runtime.crypto, initial_url)
        scope_context = _scope_context(contract, self.runtime.base_url)
        before_call_policies = tuple(
            _compile_before_call_policy(policy)
            for policy in self.runtime.before_call_policies
            if policy.scope.matches(scope_context)
        )
        challenge_policies = tuple(
            _compile_challenge_policy(policy)
            for policy in self.runtime.challenge_policies
            if policy.scope.matches(scope_context)
        )
        compiled_contract = (
            replace(
                contract,
                crypto=initial_crypto[0],
                crypto_wire=initial_crypto[1],
            )
            if initial_crypto is not None
            else contract
        )
        try:
            compiled: Any = compile_endpoint(
                compiled_contract,
                scope=compiled_contract.scope,
                requirements=wire_requirements(compiled_contract),
                fingerprint_context=(
                    *self.runtime.models.fingerprint_components(),
                    *_protection_fingerprint_components(
                        before_call_policies,
                        challenge_policies,
                    ),
                ),
                private_bindings=(
                    tuple(item.apply for item in before_call_policies)
                    + tuple(item.apply for item in challenge_policies)
                ),
            )
        except PlanError as exc:
            if "private" not in str(exc):
                raise
            raise ProtectionConfigurationError(str(exc)) from exc
        initial_compiled_crypto = _compile_http_crypto(
            compiled,
            initial_crypto,
            self.runtime.models,
            allow_async=self.runtime.allow_async_crypto,
        )
        # Preflight deliberately precedes binding-side effects and every provider.
        validate_profile(compiled.plan.requirements, self.runtime.handler_profile)
        mandatory = _validate_mandatory_protections(
            contract,
            compiled,
            self.runtime.operation_protections,
            self.runtime.solver_bindings,
            self.runtime.models,
        )
        for challenge_policy_item in challenge_policies:
            if self.runtime.solver_bindings.get(challenge_policy_item.solver) is None:
                raise MissingSolverError(
                    f"missing solver: {challenge_policy_item.solver.name}"
                )
        for before_policy_item in before_call_policies:
            if (
                before_policy_item.solver is not None
                and self.runtime.solver_bindings.get(before_policy_item.solver) is None
            ):
                raise MissingSolverError(
                    f"missing solver: {before_policy_item.solver.name}"
                )
        context = CallMiddlewareContext[T](
            compiled.plan.operation, _normalize_arguments(compiled, arguments)
        )
        registrations = (*self.runtime.middleware, *selected.middleware)
        call_chain = tuple(
            item
            for item in registrations
            if isinstance(item, CallMiddlewareRegistration)
            and item.scope.matches(_scope_context(contract, self.runtime.base_url))
        )

        async def terminal(current: CallMiddlewareContext[T]) -> ExecutionResult[T]:
            rebound = bind_plan(cast(Any, compiled.plan), current.arguments)
            operation_id = compiled.contract.operation_id
            stack = _operation_stack.get()
            if operation_id in stack:
                from eazy_sdk.auth.session_runtime import ResolutionCycleError

                raise ResolutionCycleError(
                    "operation cycle: " + " -> ".join((*stack, operation_id))
                )
            token = _operation_stack.set((*stack, operation_id))
            try:
                return await self._attempts(
                    compiled,
                    rebound,
                    selected,
                    registrations,
                    before_call_policies,
                    challenge_policies,
                    mandatory,
                    contract,
                    initial_crypto,
                    initial_compiled_crypto,
                )
            finally:
                _operation_stack.reset(token)

        callback: Callable[[CallMiddlewareContext[T]], Awaitable[Any]] = terminal
        for registration in reversed(call_chain):
            following = callback

            async def invoke(
                current: CallMiddlewareContext[T],
                registration: CallMiddlewareRegistration = registration,
                following: Callable[[CallMiddlewareContext[T]], Awaitable[Any]] = following,
            ) -> Any:
                result = registration.implementation(current, SingleUseNext(following))
                return await _maybe_await(result)

            callback = invoke
        result = await callback(context)
        if not isinstance(result, ExecutionResult):
            raise TypeError("call middleware short-circuit must return ExecutionResult")
        return cast(ExecutionResult[T], result)

    async def _attempts[T](
        self,
        compiled: Any,
        bound: OperationValues,
        options: Any,
        registrations: tuple[object, ...],
        before_call_policies: tuple[_CompiledBeforeCallPolicy, ...],
        challenge_policies: tuple[_CompiledChallengePolicy, ...],
        mandatory: _MandatoryPreparation | None,
        contract: _OperationDeclaration[T],
        initial_crypto: tuple[PayloadCrypto, HttpEncrypted] | None,
        initial_compiled_crypto: CompiledPayloadCrypto | None,
    ) -> ExecutionResult[T]:
        values = bound
        initial_url = _contract_url(self.runtime.base_url, compiled.contract.path)
        mandatory_results = (
            await self._acquire_mandatory_protections(
                compiled,
                values,
                options,
                mandatory,
            )
            if mandatory is not None
            else {}
        )
        dependencies = _DependencyCaches()
        # Each policy replays only on its own budget; the hard limit is their sum.
        replay_remaining = {item.identity: item.replay.max_replays for item in challenge_policies}
        call_states: dict[str, _ManagedProtectionState] = {}
        applied_shared: dict[str, tuple[_ProtectionCacheKey, _ManagedProtectionState]] = {}
        hard_attempt_limit = options.max_attempts + sum(replay_remaining.values())
        transport_remaining = options.transport_retries
        retry_number = 0
        auth_remaining = options.auth_retries
        redirect_remaining = options.max_redirects
        next_url: str | None = None
        redirect_method: str | None = None
        redirect_omit_body = False
        attempt_kind = "initial"
        for number in range(1, hard_attempt_limit + 1):
            self._observe("start_attempt", {"number": number, "kind": attempt_kind})
            dependencies.attempt.clear()
            attempt_values = values
            current_url = next_url or initial_url
            selected_crypto = _resolve_http_crypto(contract, self.runtime.crypto, current_url)
            compiled_crypto = (
                initial_compiled_crypto
                if selected_crypto == initial_crypto
                else _compile_http_crypto(
                    compiled,
                    selected_crypto,
                    self.runtime.models,
                    allow_async=self.runtime.allow_async_crypto,
                )
            )
            dependency_patch = await _resolve_requirements(
                _lower_requirements(
                    (*compiled.contract.requires, *compiled.contract.inject),
                    compiled,
                    self.runtime.dependencies,
                ),
                self.runtime.dependencies,
                operation_id=compiled.contract.operation_id,
                attempt=number,
                caches=dependencies,
            )
            crypto_values = CryptoValues()
            crypto_aad: tuple[tuple[str, FrozenValue], ...] = ()
            if compiled_crypto is not None and compiled_crypto.profile.inputs:
                crypto_values, crypto_aad = await resolve_crypto_inputs(
                    compiled_crypto.profile.inputs,
                    self.runtime.dependencies,
                    operation_id=compiled.contract.operation_id,
                    attempt=number,
                )
            auth_executions, auth_patch = await resolve_security(
                compiled.contract.security,
                self.runtime.auth,
                cast(Any, compiled),
                graph=self.resolution_graph,
            )
            scope = _scope_context(compiled.contract, self.runtime.base_url, current_url)
            attempts = tuple(
                item
                for item in registrations
                if isinstance(item, AttemptMiddlewareRegistration) and item.scope.matches(scope)
            )
            patches = [dependency_patch, auth_patch]
            for registration in attempts:
                contribute = getattr(registration.implementation, "contribute", None)
                if contribute is not None:
                    patch = await _maybe_await(
                        contribute(AttemptRequestContext(compiled.plan.operation, number))
                    )
                    if patch is not None:
                        patches.append(patch)
            attempt_values = apply_patch_atomic(
                attempt_values,
                ValuePatch(tuple(op for patch in patches for op in patch.operations)),
            )
            # Managed protection state is applied only after every public write of
            # this attempt is known, so the transport identity it is checked against
            # (User-Agent, proxy, impersonation) is the one the request will carry.
            attempt_identity = _transport_identity(
                self.runtime,
                _slot_headers(compiled, attempt_values),
            )
            attempt_fingerprint = attempt_identity.fingerprint()
            for before_policy_item in before_call_policies:
                state, shared = await self._before_call_state(
                    before_policy_item,
                    compiled,
                    attempt_values,
                    options,
                    number,
                    call_states,
                    attempt_identity,
                )
                attempt_values = _apply_managed_state(
                    compiled,
                    attempt_values,
                    before_policy_item.apply,
                    state,
                    policy=before_policy_item.identity,
                )
                if shared is not None:
                    applied_shared[before_policy_item.identity] = shared
            for challenge_policy_item in challenge_policies:
                local = call_states.get(challenge_policy_item.identity)
                if local is not None and not _identity_matches(local, attempt_fingerprint):
                    call_states.pop(challenge_policy_item.identity, None)
                    local = None
                shared = _find_shared_state(
                    self.runtime,
                    challenge_policy_item,
                    attempt_fingerprint,
                )
                selected_state = local or (shared[1] if shared is not None else None)
                if selected_state is None:
                    continue
                attempt_values = _apply_managed_state(
                    compiled,
                    attempt_values,
                    challenge_policy_item.apply,
                    selected_state,
                    policy=challenge_policy_item.identity,
                )
                if shared is not None and (local is None or local is shared[1]):
                    applied_shared[challenge_policy_item.identity] = shared
                if challenge_policy_item.persistence.mode in {
                    ProtectionPersistenceMode.PER_MATCH,
                    ProtectionPersistenceMode.PER_ATTEMPT,
                }:
                    call_states.pop(challenge_policy_item.identity, None)
            if self.runtime.limiter is not None:
                decision = await _maybe_await(
                    self.runtime.limiter.reserve(
                        RateLimitContext(
                            compiled.plan.operation,
                            compiled.contract.method,
                            urlsplit(current_url).netloc,
                            number,
                            attempt_kind,
                        )
                    )
                )
                if decision.delay > 0:
                    await asyncio.sleep(decision.delay)
                self._observe("rate_limit", number)
            signature_plan = compiled.signature_plan
            crypto_outputs: list[CryptoOutputValue[object]] = []
            body_document_override = build_request_document(
                RequestDocumentStageInput(
                    compiled,
                    attempt_values,
                    self.runtime.models,
                    mandatory_results,
                )
            ).document
            if (
                compiled_crypto is not None
                and compiled_crypto.outbound_fields
                and not redirect_omit_body
            ):
                if body_document_override is _NO_BODY_DOCUMENT_OVERRIDE:
                    body_slot = compiled.body_slot
                    if body_slot is None:
                        raise CryptoConfigurationError(
                            "outbound field crypto requires a semantic JSON request body"
                        )
                    body_document_override = self.runtime.models.dump(
                        attempt_values.require(body_slot)
                    )
                if body_document_override is _NO_BODY_DOCUMENT_OVERRIDE:
                    raise CryptoConfigurationError(
                        "outbound field crypto requires a semantic JSON request body"
                    )
                context = _http_crypto_context(
                    compiled_crypto,
                    selected_crypto,
                    compiled.contract.operation_id,
                    redirect_method or compiled.contract.method,
                    current_url,
                    number,
                    CryptoDirection.OUTBOUND,
                    CryptoStage.DOCUMENT,
                    values=crypto_values,
                    aad=crypto_aad,
                )
                body_document_override = await prepare_http_document(
                    body_document_override,
                    compiled_crypto,
                    context=context,
                    outputs=crypto_outputs,
                )
            try:
                unsigned = RequestPreparer(
                    self.runtime.base_url,
                    self.runtime.profile,
                    self.runtime.models,
                ).prepare(
                    compiled,
                    attempt_values,
                    reserved_outputs=reserve_outputs(signature_plan),
                    url_override=next_url,
                    method_override=redirect_method,
                    omit_body=redirect_omit_body,
                    body_document_override=body_document_override,
                )
            except BindingError:
                raise OperationBindingError(
                    code="preparation_failed",
                    operation_id=compiled.contract.operation_id,
                    field=None,
                    phase="prepare",
                    detail="request values could not be prepared",
                ) from None
            if (
                compiled_crypto is not None
                and compiled_crypto.profile.outbound is not None
                and (
                    compiled_crypto.profile.outbound.encoded is not None
                    or (selected_crypto is not None and selected_crypto[1].metadata)
                )
                and not redirect_omit_body
            ):
                assert selected_crypto is not None
                unsigned = await protect_http_request(
                    unsigned,
                    compiled_crypto,
                    selected_crypto[1],
                    context=_http_crypto_context(
                        compiled_crypto,
                        selected_crypto,
                        compiled.contract.operation_id,
                        redirect_method or compiled.contract.method,
                        current_url,
                        number,
                        CryptoDirection.OUTBOUND,
                        CryptoStage.ENCODED,
                        values=crypto_values,
                        aad=crypto_aad,
                    ),
                    outputs=crypto_outputs,
                )
            if signature_plan.signatures:
                if self.runtime.key_provider is None:
                    raise ValueError("signing key provider is not configured")
                prepared = sign_prepared(unsigned, signature_plan, self.runtime.key_provider)
                if isinstance(prepared.body, BufferedBody):
                    media_type = (
                        prepared.body.content_type.decode("ascii")
                        if prepared.body.content_type is not None
                        else None
                    )
                    prepared = replace(
                        prepared,
                        body_input=ExactBodyInput(prepared.body.content, media_type),
                    )
            else:
                prepared = unsigned.finalize()
            self._observe(
                "prepared",
                PreparedRequestSummary(
                    prepared.method.decode("ascii"),
                    prepared.target.partition(b"?")[0].decode("ascii"),
                    len(prepared.body.content) if hasattr(prepared.body, "content") else 0,
                ),
            )
            for registration in attempts:
                before_emit = getattr(registration.implementation, "before_emit", None)
                if before_emit is not None:
                    decision = before_emit(
                        PreparedAttemptContext(compiled.plan.operation, number, prepared)
                    )
                    if isinstance(decision, Fail):
                        raise decision.error
            try:
                response = cast(
                    NormalizedResponse[Any],
                    await _maybe_await(self.runtime.send(prepared, options=options.emit_options())),
                )
                self._observe("emit", number)
            except TransportError as error:
                proposed = None
                for registration in attempts:
                    hook = getattr(registration.implementation, "on_transport_error", None)
                    if hook is not None:
                        decision = await _maybe_await(
                            hook(
                                AttemptTransportErrorContext(compiled.plan.operation, number, error)
                            )
                        )
                        if isinstance(decision, Fail):
                            raise decision.error from None
                        if isinstance(decision, ProposeAction):
                            proposed = decision.action
                if transport_remaining > 0 or proposed is not None:
                    if not compiled.contract.is_idempotent:
                        if options.retry.retries:
                            from eazy_sdk.clients.base import UnsafeReplayError

                            raise UnsafeReplayError(
                                "retry policy requires an idempotent operation"
                            ) from error
                        raise
                    if transport_remaining > 0:
                        transport_remaining -= 1
                        retry_number += 1
                        await options.retry.wait(retry_number)
                    attempt_kind = "transport-retry"
                    continue
                raise
            if compiled_crypto is not None and compiled_crypto.profile.inbound is not None:
                assert selected_crypto is not None
                response = await unprotect_http_response(
                    response,
                    compiled_crypto,
                    selected_crypto[1],
                    context=_http_crypto_context(
                        compiled_crypto,
                        selected_crypto,
                        compiled.contract.operation_id,
                        redirect_method or compiled.contract.method,
                        current_url,
                        number,
                        CryptoDirection.INBOUND,
                        CryptoStage.ENCODED,
                        clear_content_type=selected_crypto[1].clear_content_type,
                        outer_content_type=response.content_type,
                        values=crypto_values,
                        aad=crypto_aad,
                    ),
                )
            response_context = _response_context(
                response,
                prepared,
                compiled.contract.operation_id,
                number,
                models=self.runtime.models,
            )
            proposed_response: object | None = None
            for registration in attempts:
                after = getattr(registration.implementation, "after_response", None)
                if after is not None:
                    decision = await _maybe_await(
                        after(
                            AttemptResponseContext(
                                compiled.plan.operation,
                                number,
                                cast(ResponseContext[object], response_context),
                            )
                        )
                    )
                    if isinstance(decision, Fail):
                        raise decision.error
                    if isinstance(decision, ReplaceResponse):
                        response = decision.response
                        response_context = _response_context(
                            response,
                            prepared,
                            compiled.contract.operation_id,
                            number,
                            models=self.runtime.models,
                        )
                    if isinstance(decision, ProposeAction):
                        proposed_response = decision.action
            signal = _inspect_signals(
                cast(Any, tuple(policy.signal for policy in challenge_policies)),
                cast(ResponseContext[object], response_context),
                scope,
            )
            if isinstance(signal, MalformedSignal):
                raise ChallengeParseError(
                    _policy_identity(challenge_policies, signal.signal),
                    number,
                ) from signal.cause
            if isinstance(signal, AmbiguousSignal):
                raise AmbiguousChallengeError(
                    tuple(_policy_identity(challenge_policies, item) for item in signal.signals),
                    number,
                )
            outcome = (
                compiled.contract.responses.inspect(response_context)
                if isinstance(compiled.contract.responses, Responses)
                else None
            )
            response_decision = decide_response(
                ResponseDecisionInput(
                    response=cast(NormalizedResponse[object], response),
                    proposed=proposed_response,
                    signal=signal,
                    outcome=outcome,
                    idempotent=compiled.contract.is_idempotent,
                    attempt=number,
                    hard_attempt_limit=hard_attempt_limit,
                    transport_remaining=transport_remaining,
                    retry_statuses=options.retry.retry_statuses,
                    redirect_remaining=redirect_remaining,
                    auth_remaining=auth_remaining,
                    auth_refreshable=_has_refreshable_security(
                        auth_executions,
                        self.runtime.auth,
                    ),
                    current_url=current_url,
                    effective_method=redirect_method or compiled.contract.method,
                    raw_response=compiled.contract.raw_response,
                )
            )
            if isinstance(response_decision, RetryTransition):
                if response_decision.patch is not None:
                    values = apply_patch_atomic(values, response_decision.patch)
                if response_decision.consumes_transport:
                    transport_remaining -= 1
                    retry_number += 1
                    await options.retry.wait(retry_number)
                attempt_kind = response_decision.kind
                continue
            if isinstance(response_decision, RedirectTransition):
                redirect_remaining -= 1
                next_url = response_decision.url
                if response_decision.method is not None:
                    redirect_method = response_decision.method
                if response_decision.omit_body:
                    redirect_omit_body = True
                attempt_kind = "redirect"
                continue
            if isinstance(response_decision, ReactionTransition):
                signal_match = response_decision.match
                matched_policy = next(
                    item for item in challenge_policies if item.signal is signal_match.signal
                )
                _ensure_replay_allowed(
                    cast(Any, compiled),
                    attempt_values,
                    prepared.body,
                    matched_policy.replay,
                    origin_may_have_executed=True,
                    remaining=replay_remaining[matched_policy.identity],
                )
                replay_remaining[matched_policy.identity] -= 1
                state, shared = await self._challenge_state(
                    matched_policy,
                    signal_match.value,
                    cast(ResponseContext[object], response_context),
                    compiled,
                    attempt_values,
                    options,
                    number,
                    call_states,
                    applied_shared.get(matched_policy.identity),
                    attempt_identity,
                    _prepared_headers(prepared),
                )
                call_states[matched_policy.identity] = state
                if shared is not None:
                    applied_shared[matched_policy.identity] = shared
                attempt_kind = "reaction"
                continue
            if isinstance(response_decision, AuthRefreshTransition):
                await _refresh_security(
                    auth_executions,
                    self.runtime.auth,
                    self.resolution_graph,
                )
                auth_remaining -= 1
                attempt_kind = "auth-refresh"
                continue
            if isinstance(response_decision, TerminalResponse):
                return ExecutionResult(
                    cast(T, response_decision.value),
                    cast(NormalizedResponse[Any], response_decision.response),
                )
            assert isinstance(response_decision, RejectedResponse)
            response_decision.outcome.unwrap()
            raise AssertionError("terminal response outcome unexpectedly returned")
        from eazy_sdk.clients.base import AttemptLimitError

        raise AttemptLimitError("hard attempt budget exhausted")

    async def _before_call_state(
        self,
        policy: _CompiledBeforeCallPolicy,
        compiled: Any,
        values: OperationValues,
        options: Any,
        attempt: int,
        call_states: dict[str, _ManagedProtectionState],
        identity: TransportIdentity,
    ) -> tuple[
        _ManagedProtectionState,
        tuple[_ProtectionCacheKey, _ManagedProtectionState] | None,
    ]:
        fingerprint = identity.fingerprint()
        mode = policy.persistence.mode
        if mode is ProtectionPersistenceMode.PER_CALL and policy.identity in call_states:
            local = call_states[policy.identity]
            if _identity_matches(local, fingerprint):
                return local, None
            call_states.pop(policy.identity, None)
        shared = _find_shared_state(self.runtime, policy, fingerprint)
        if shared is not None:
            return shared[1], shared

        solver: ChallengeSolver[Any, Any] | None = None
        if policy.solver is not None:
            solver = self.runtime.solver_bindings.get(policy.solver)
            assert solver is not None
        key = _protection_cache_key(
            self.runtime,
            policy,
            solver,
            policy.challenge,
        )

        async def acquire() -> object:
            if policy.acquire is not None:
                acquired = await self.execute(policy.acquire.call({}), options=options)
                solution = acquired.value
            else:
                assert solver is not None and policy.challenge is not None
                try:
                    solution = await solver.solve(
                        policy.challenge,
                        _solve_context(
                            self.runtime,
                            options,
                            compiled.plan.operation,
                            None,
                            attempt,
                            identity,
                            _slot_headers(compiled, values),
                        ),
                    )
                except Exception as exc:
                    raise ChallengeSolveError(policy.identity, attempt) from exc
            return solution

        if _is_shared(mode):
            state, committed_key = await self._shared_state(
                key,
                policy.persistence,
                policy.apply,
                compiled,
                values,
                acquire,
                fingerprint,
            )
            if committed_key is not None:
                return state, (committed_key, state)
        else:
            solution = await acquire()
            state = _new_managed_state(self.runtime, solution, fingerprint)
            _apply_managed_state(
                compiled,
                values,
                policy.apply,
                state,
                policy=policy.identity,
            )
        if mode is ProtectionPersistenceMode.PER_CALL:
            call_states[policy.identity] = state
        return state, None

    async def _challenge_state(
        self,
        policy: _CompiledChallengePolicy,
        challenge: object,
        response: ResponseContext[object],
        compiled: Any,
        values: OperationValues,
        options: Any,
        attempt: int,
        call_states: dict[str, _ManagedProtectionState],
        rejected: tuple[_ProtectionCacheKey, _ManagedProtectionState] | None,
        identity: TransportIdentity,
        request_headers: Mapping[str, str],
    ) -> tuple[
        _ManagedProtectionState,
        tuple[_ProtectionCacheKey, _ManagedProtectionState] | None,
    ]:
        solver = self.runtime.solver_bindings.get(policy.solver)
        assert solver is not None
        key = _protection_cache_key(
            self.runtime,
            policy,
            solver,
            challenge,
        )
        context = _solve_context(
            self.runtime,
            options,
            compiled.plan.operation,
            response,
            attempt,
            identity,
            request_headers,
        )
        fingerprint = identity.fingerprint()

        async def solve() -> object:
            try:
                return await solver.solve(challenge, context)
            except Exception as exc:
                raise ChallengeSolveError(policy.identity, attempt) from exc

        if _is_shared(policy.persistence.mode):
            state, committed_key = await self._shared_state(
                key,
                policy.persistence,
                policy.apply,
                compiled,
                values,
                solve,
                fingerprint,
                rejected=rejected,
            )
            return state, (
                (committed_key, state) if committed_key is not None else None
            )
        call_states.pop(policy.identity, None)
        solution = await solve()
        state = _new_managed_state(self.runtime, solution, fingerprint)
        _apply_managed_state(
            compiled,
            values,
            policy.apply,
            state,
            policy=policy.identity,
        )
        return state, None

    async def _shared_state(
        self,
        key: _ProtectionCacheKey,
        persistence: ProtectionPersistence,
        bindings: PrivateBindings[Any],
        compiled: Any,
        values: OperationValues,
        acquire: Callable[[], Awaitable[object]],
        fingerprint: str,
        *,
        rejected: tuple[_ProtectionCacheKey, _ManagedProtectionState] | None = None,
    ) -> tuple[_ManagedProtectionState, _ProtectionCacheKey | None]:
        async with self.runtime._protection_locks.hold(key):
            if rejected is not None:
                rejected_key, rejected_state = rejected
                if self.runtime._protection_state.get(rejected_key) is rejected_state:
                    self.runtime._protection_state.pop(rejected_key, None)
            cached = self.runtime._protection_state.get(key)
            if cached is not None and _managed_state_valid(
                cached, persistence.mode, fingerprint
            ):
                return cached, key
            self.runtime._protection_state.pop(key, None)
            solution = await acquire()
            state = _new_managed_state(self.runtime, solution, fingerprint)
            # Validate the entire batch before publishing any reusable state.
            _apply_managed_state(
                compiled,
                values,
                bindings,
                state,
                policy=str(key[0]),
            )
            if _solution_is_shareable(solution, persistence.mode):
                self.runtime._protection_state[key] = state
                return state, key
            return state, None

    async def _acquire_mandatory_protections(
        self,
        compiled: Any,
        values: OperationValues,
        options: Any,
        preparation: _MandatoryPreparation,
    ) -> dict[int, object]:
        results: dict[int, object] = {}
        for flow, acquire, verify in preparation.flows:
            acquired = await self.execute(acquire.call({}), options=options)
            current: object = acquired.value
            if flow.solve:
                solver = self.runtime.solver_bindings.get(flow.requirement)
                assert solver is not None
                try:
                    headers = _slot_headers(compiled, values)
                    current = await solver.solve(
                        current,
                        _solve_context(
                            self.runtime,
                            options,
                            compiled.plan.operation,
                            None,
                            0,
                            _transport_identity(self.runtime, headers),
                            headers,
                        ),
                    )
                except Exception as exc:
                    raise ChallengeSolveError(flow.requirement.name, 0) from exc
            if verify is not None:
                field = verify.input_fields[0]
                verified = await self.execute(
                    verify.call({field.python_name: current}),
                    options=options,
                )
                current = verified.value
            results[id(flow.requirement)] = current
        return results

    def _observe(self, phase: str, value: object | None = None) -> None:
        if self.runtime.observer is not None:
            self.runtime.observer(phase, value)


def _protection_identities(
    targets: tuple[InstallableProtection | str, ...],
) -> frozenset[str] | None:
    """Policy identities addressed by guards/names; ``None`` means every policy."""

    if not targets:
        return None
    identities: set[str] = set()
    for target in targets:
        if isinstance(target, str):
            if not target:
                raise ValueError("protection identity must not be empty")
            identities.add(target)
            continue
        if not isinstance(target, InstallableProtection):
            raise TypeError("invalidate_protection accepts guards or policy identities")
        bundle = target.to_bundle()
        identities.update(policy.identity for policy in bundle.challenge_policies)
        identities.update(policy.identity for policy in bundle.before_call_policies)
    return frozenset(identities)


def _protection_fingerprint_components(
    before: tuple[_CompiledBeforeCallPolicy, ...],
    challenge: tuple[_CompiledChallengePolicy, ...],
) -> tuple[str, ...]:
    components: list[str] = []
    for lifecycle, policies in (("before", before), ("challenge", challenge)):
        for policy in policies:
            components.append(
                ":".join(
                    (
                        "protection",
                        lifecycle,
                        policy.identity,
                        str(policy.revision),
                    )
                )
            )
    return tuple(components)


def _policy_identity(
    policies: tuple[_CompiledChallengePolicy, ...],
    signal: object,
) -> str:
    for policy in policies:
        if policy.signal is signal:
            return policy.identity
    return getattr(signal, "name", "<unknown>")


def _compile_challenge_policy(
    policy: ChallengePolicy[Any, Any],
) -> _CompiledChallengePolicy:
    # ``ChallengePolicy`` validates its own fields at construction.
    return _CompiledChallengePolicy(
        identity=policy.identity,
        revision=policy.revision,
        signal=policy.signal,
        solver=policy.solver,
        apply=policy.apply,
        persistence=policy.persistence,
        replay=policy.replay,
        challenge_identity=policy.challenge_identity,
    )


def _compile_before_call_policy(
    policy: BeforeCallPolicy[Any, Any],
) -> _CompiledBeforeCallPolicy:
    # ``BeforeCallPolicy`` validates its own fields at construction.
    acquire = (
        _operation_reference(policy.acquire, role="before-call acquire")
        if policy.acquire is not None
        else None
    )
    return _CompiledBeforeCallPolicy(
        identity=policy.identity,
        revision=policy.revision,
        acquire=acquire,
        challenge=policy.challenge,
        solver=policy.solver,
        apply=policy.apply,
        persistence=policy.persistence,
    )


def _apply_managed_state(
    compiled: Any,
    values: OperationValues,
    bindings: PrivateBindings[Any],
    state: _ManagedProtectionState,
    *,
    policy: str,
) -> OperationValues:
    try:
        patch = _private_bindings_patch(cast(Any, compiled), bindings, state.solution)
        return apply_patch_atomic(values, patch)
    except Exception as exc:
        raise ChallengeApplicationError(policy) from exc


def _new_managed_state(
    runtime: ExecutionRuntime,
    solution: object,
    identity: str | None,
) -> _ManagedProtectionState:
    runtime._protection_generation += 1
    return _ManagedProtectionState(solution, runtime._protection_generation, identity)


def _identity_matches(state: _ManagedProtectionState, fingerprint: str | None) -> bool:
    """A solution acquired under one transport identity never applies to another."""

    return state.identity is None or fingerprint is None or state.identity == fingerprint


def _is_shared(mode: ProtectionPersistenceMode) -> bool:
    return mode in {
        ProtectionPersistenceMode.UNTIL_EXPIRY,
        ProtectionPersistenceMode.UNTIL_REJECTED,
    }


def _solution_is_shareable(solution: object, mode: ProtectionPersistenceMode) -> bool:
    if mode is ProtectionPersistenceMode.UNTIL_EXPIRY:
        return _has_future_expiry(solution)
    if mode is ProtectionPersistenceMode.UNTIL_REJECTED:
        expires = getattr(solution, "expires_at", None)
        return not isinstance(expires, datetime) or _not_expired(solution)
    return False


def _managed_state_valid(
    state: _ManagedProtectionState,
    mode: ProtectionPersistenceMode,
    fingerprint: str | None,
) -> bool:
    return _identity_matches(state, fingerprint) and _solution_is_shareable(
        state.solution, mode
    )


def _protection_cache_key(
    runtime: ExecutionRuntime,
    policy: _CompiledChallengePolicy | _CompiledBeforeCallPolicy,
    solver: ChallengeSolver[Any, Any] | None,
    challenge: object | None,
) -> _ProtectionCacheKey:
    if isinstance(policy, _CompiledChallengePolicy):
        challenge_identity = (
            policy.challenge_identity(challenge)
            if policy.challenge_identity is not None
            else None
        )
    else:
        challenge_identity = None
    try:
        hash(challenge_identity)
    except TypeError as exc:
        raise TypeError("protection challenge identity must be hashable") from exc
    return (
        *_protection_cache_prefix(runtime, policy, solver),
        challenge_identity,
    )


def _protection_cache_prefix(
    runtime: ExecutionRuntime,
    policy: _CompiledChallengePolicy | _CompiledBeforeCallPolicy,
    solver: ChallengeSolver[Any, Any] | None,
) -> _ProtectionCacheKey:
    if isinstance(policy, _CompiledChallengePolicy):
        provider_identity = policy.solver
    else:
        provider_identity = policy.solver or policy.acquire
    requirement_identity = id(provider_identity)
    scope = policy.persistence.scope.scope
    owner: object
    if scope is ProtectionStateScope.CLIENT:
        owner = id(runtime)
    else:
        session_owner = runtime.protection_session_owner
        owner = _hashable_identity(runtime if session_owner is None else session_owner)
    return (
        policy.identity,
        policy.revision,
        requirement_identity,
        id(solver) if solver is not None else id(provider_identity),
        scope,
        owner,
    )


def _hashable_identity(value: object) -> object:
    try:
        hash(value)
    except TypeError:
        return (type(value), id(value))
    return value


def _find_shared_state(
    runtime: ExecutionRuntime,
    policy: _CompiledChallengePolicy | _CompiledBeforeCallPolicy,
    fingerprint: str | None,
) -> tuple[_ProtectionCacheKey, _ManagedProtectionState] | None:
    if not _is_shared(policy.persistence.mode):
        return None
    solver = (
        runtime.solver_bindings.get(policy.solver)
        if policy.solver is not None
        else None
    )
    prefix = _protection_cache_prefix(runtime, policy, solver)
    candidates: list[tuple[_ProtectionCacheKey, _ManagedProtectionState]] = []
    for key, state in tuple(runtime._protection_state.items()):
        if key[:-1] != prefix:
            continue
        if _managed_state_valid(state, policy.persistence.mode, fingerprint):
            candidates.append((key, state))
        else:
            # Expired, or acquired under another transport identity: a clearance
            # bound to one proxy/User-Agent must never be replayed through another.
            runtime._protection_state.pop(key, None)
    return max(candidates, key=lambda item: item[1].generation) if candidates else None


def _validate_mandatory_protections(
    declaration: _OperationDeclaration[Any],
    compiled: Any,
    configured: tuple[object, ...],
    solvers: SolverBindings,
    models: ModelAdapterRegistry,
) -> _MandatoryPreparation | None:
    requirements = declaration.protections
    if not requirements:
        return None
    flows: list[
        tuple[
            ProtectionFlow[Any],
            _OperationDeclaration[Any],
            _OperationDeclaration[Any] | None,
        ]
    ] = []
    for requirement in requirements:
        matches = [
            item
            for item in configured
            if isinstance(item, ProtectionFlow) and item.requirement is requirement
        ]
        if len(matches) != 1:
            detail = "missing" if not matches else "duplicate"
            raise TypeError(f"{detail} protection flow: {requirement.name}")
        flow = matches[0]
        if flow.solve and solvers.get(requirement) is None:
            raise MissingSolverError(f"missing solver: {requirement.name}")
        acquire = _operation_reference(flow.acquire, role="acquire")
        if acquire.input_fields:
            raise TypeError("protection acquire operation must not require arguments")
        verify = (
            _operation_reference(flow.verify, role="verify") if flow.verify is not None else None
        )
        if verify is not None and len(verify.input_fields) != 1:
            raise TypeError("protection verify operation must declare exactly one input")
        flows.append((flow, acquire, verify))

    writers = compiled.private_wire_writers
    mapped = {id(writer.requirement) for writer in writers}
    declared = {id(requirement) for requirement in requirements}
    if mapped != declared:
        raise TypeError("body projection protection writers do not match operation protections")
    by_requirement = {
        id(flow.requirement): (flow, acquire, verify) for flow, acquire, verify in flows
    }
    for writer in writers:
        flow, acquire, verify = by_requirement[id(writer.requirement)]
        result_type = _flow_result_type(flow, acquire, verify, solvers)
        if not _result_has_field(result_type, writer.result_field, models):
            raise TypeError(
                f"{'.'.join(writer.path)}: protection result has no field "
                f"{writer.result_field!r}"
            )
    return _MandatoryPreparation(tuple(flows), writers)


def _operation_reference(value: object, *, role: str) -> _OperationDeclaration[Any]:
    if isinstance(value, _OperationDeclaration):
        return value
    declaration = getattr(value, "declaration", None)
    if isinstance(declaration, _OperationDeclaration):
        return declaration
    raise TypeError(f"protection {role} must reference a decorated operation")


def _flow_result_type(
    flow: ProtectionFlow[Any],
    acquire: _OperationDeclaration[Any],
    verify: _OperationDeclaration[Any] | None,
    solvers: SolverBindings,
) -> object:
    if verify is not None:
        return verify.result_type
    if not flow.solve:
        return acquire.result_type
    solver = solvers.get(flow.requirement)
    assert solver is not None
    try:
        return get_type_hints(solver.solve)["return"]
    except (KeyError, NameError, TypeError) as exc:
        raise TypeError(
            f"solver for {flow.requirement.name!r} requires a return annotation"
        ) from exc


def _result_has_field(result_type: object, field_name: str, models: ModelAdapterRegistry) -> bool:
    if isinstance(result_type, type):
        try:
            return any(field.name == field_name for field in models.fields(result_type))
        except TypeError:
            pass
    annotations = getattr(result_type, "__annotations__", None)
    return isinstance(annotations, Mapping) and field_name in annotations


def _contract_url(base_url: str, path: str) -> str:
    if urlsplit(path).scheme:
        return path
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _normalize_arguments(compiled: Any, arguments: BoundArguments) -> BoundArguments:
    by_name = {slot.diagnostic_name: slot for slot in compiled.plan.shape.slots}
    normalized: list[Bind[Any]] = []
    for binding in arguments.bindings:
        slot = by_name.get(binding.slot.diagnostic_name)
        if slot is None:
            raise ValueError(f"argument slot is not in this plan: {binding.slot.diagnostic_name}")
        normalized.append(Bind(slot, binding.value))
    return BoundArguments(tuple(normalized))


def _scope_context(
    contract: _OperationDeclaration[Any], base_url: str, url: str | None = None
) -> ScopeContext:
    split = urlsplit(url or _contract_url(base_url, contract.path))
    return ScopeContext(
        split.scheme,
        split.netloc,
        split.path,
        contract.method,
        OperationIdentity(contract.operation_id),
    )


def _resolve_http_crypto(
    contract: _OperationDeclaration[Any],
    registry: CryptoRegistry,
    url: str,
) -> tuple[PayloadCrypto, HttpEncrypted] | None:
    profile = contract.crypto
    wire: object = contract.crypto_wire
    if profile is None:
        if not contract.crypto_inherit:
            return None
        split = urlsplit(url)
        resolved = registry.resolve_http(
            host=split.hostname or "",
            path=split.path or "/",
            method=contract.method,
            operation_id=contract.operation_id,
        )
        if resolved is None:
            return None
        profile = resolved.profile
        wire = resolved.wire
    if not isinstance(profile, PayloadCrypto):
        raise CryptoConfigurationError("HTTP operation requires a PayloadCrypto profile")
    if wire is None:
        selected_wire = HttpEncrypted()
    elif isinstance(wire, HttpEncrypted):
        selected_wire = wire
    else:
        raise CryptoConfigurationError("HTTP operation requires an HttpEncrypted wire binding")
    return profile, selected_wire


def _compile_http_crypto(
    compiled: Any,
    selected: tuple[PayloadCrypto, HttpEncrypted] | None,
    models: ModelAdapterRegistry,
    *,
    allow_async: bool,
) -> CompiledPayloadCrypto | None:
    if selected is None:
        return None
    profile, wire = selected
    if any(
        item.scope is CryptoInputScope.CONNECTION for item in profile.inputs
    ):
        raise CryptoConfigurationError(
            "HTTP crypto accepts only operation-scoped inputs"
        )
    validate_crypto_runtime(profile, allow_async=allow_async)
    root_body = next((field for field in compiled.input_fields if field.is_root_body), None)
    projected_json = (
        compiled.body_projection is not None
        and isinstance(compiled.body_projection.encoding, JsonBody)
    )
    if (
        profile.outbound is not None
        and profile.outbound.fields
        and not projected_json
        and (root_body is None or not isinstance(root_body.placement, JsonBody))
    ):
        raise CryptoConfigurationError(
            "outbound field crypto requires one semantic JsonBody document"
        )
    if profile.outbound is not None and profile.outbound.encoded is not None:
        conflicts = {
            name.casefold()
            for name in compiled.header_slots
            if name.casefold() in {"content-type", "content-length"}
        }
        if conflicts:
            raise CryptoConfigurationError(
                "crypto wire binding owns HTTP representation headers: "
                + ", ".join(sorted(conflicts))
            )
        if root_body is not None and isinstance(root_body.placement, ReplayableStreamBody):
            raise CryptoStreamingUnsupportedError(
                "whole-payload crypto does not support ReplayableStreamBody"
            )
    metadata_headers = {item.name.casefold() for item in wire.metadata}
    header_conflicts = metadata_headers & {name.casefold() for name in compiled.header_slots}
    if header_conflicts:
        raise CryptoConfigurationError(
            "crypto metadata headers conflict with request inputs: "
            + ", ".join(sorted(header_conflicts))
        )
    signature_headers = {
        output.name.casefold()
        for signature in compiled.signature_plan.signatures
        for output in signature.outputs
        if output.location.value == "header"
    }
    signature_conflicts = metadata_headers & signature_headers
    if signature_conflicts:
        raise CryptoConfigurationError(
            "crypto metadata headers conflict with signing outputs: "
            + ", ".join(sorted(signature_conflicts))
        )
    declared_outputs: list[object] = []
    if profile.outbound is not None:
        for item in profile.outbound.fields:
            declared_outputs.extend(item.outputs)
        if profile.outbound.encoded is not None:
            declared_outputs.extend(profile.outbound.encoded.outputs)
    bound_outputs = {id(item.output) for item in wire.metadata}
    inbound_metadata: list[object] = []
    if profile.inbound is not None:
        for inbound_field in profile.inbound.fields:
            inbound_metadata.extend(inbound_field.metadata)
        if profile.inbound.encoded is not None:
            inbound_metadata.extend(profile.inbound.encoded.metadata)
    required_outputs = {
        id(item) for item in (*declared_outputs, *inbound_metadata)
    }
    if required_outputs != bound_outputs:
        raise CryptoConfigurationError(
            "HTTP crypto metadata bindings must exactly match declared output writes and reads"
        )
    inbound_models = _http_inbound_models(compiled.contract)
    compiled_profile = compile_payload_crypto(
        profile,
        models,
        outbound_model=(
            compiled.body_projection.target
            if compiled.body_projection is not None
            else root_body.annotation if root_body is not None else None
        ),
        inbound_models=inbound_models,
    )
    _validate_projection_writer_graph(compiled, compiled_profile)
    return compiled_profile


def _validate_projection_writer_graph(
    compiled: Any,
    crypto: CompiledPayloadCrypto,
) -> None:
    signature_paths = compiled.body_signature_paths
    if not signature_paths:
        return
    outbound = crypto.profile.outbound
    if outbound is not None and outbound.encoded is not None:
        raise CryptoConfigurationError(
            "body signature outputs cannot run after outbound encoded crypto"
        )
    crypto_paths = tuple(field.wire_path for field in crypto.outbound_fields)
    for signature_path in signature_paths:
        for crypto_path in crypto_paths:
            common = min(len(signature_path), len(crypto_path))
            if signature_path[:common] == crypto_path[:common]:
                raise CryptoConfigurationError(
                    "body signature and outbound crypto writers overlap: "
                    f"{signature_path!r} and {crypto_path!r}"
                )


def _http_inbound_models(contract: object) -> tuple[object, ...]:
    values: list[object] = []
    result_type = getattr(contract, "result_type", None)
    if result_type is not None and result_type is not object:
        values.append(result_type)
    responses = getattr(contract, "responses", None)
    for case in getattr(responses, "cases", ()):
        model = getattr(getattr(case, "response", None), "model", None)
        if model is not None and model not in values:
            values.append(model)
    return tuple(values)


def _http_crypto_context(
    compiled: CompiledPayloadCrypto,
    selected: tuple[PayloadCrypto, HttpEncrypted] | None,
    operation_id: str,
    method: str,
    url: str,
    attempt: int,
    direction: CryptoDirection,
    stage: CryptoStage,
    *,
    clear_content_type: str | None = None,
    outer_content_type: str | None = None,
    values: CryptoValues | None = None,
    aad: tuple[tuple[str, FrozenValue], ...] = (),
) -> HttpCryptoContext:
    if selected is None:
        raise AssertionError("crypto context requires a selected profile")
    _, wire = selected
    split = urlsplit(url)
    return HttpCryptoContext(
        operation_id,
        compiled.profile.name,
        "pending",
        direction,
        stage,
        attempt,
        aad=aad,
        values=values or CryptoValues(),
        method=method,
        authority=split.netloc,
        clear_content_type=clear_content_type or wire.clear_content_type,
        outer_content_type=outer_content_type or wire.content_type,
    )


def _response_context(
    response: NormalizedResponse[Any],
    prepared: Any,
    operation_id: str,
    attempt: int,
    *,
    models: ModelAdapterRegistry,
) -> ResponseContext[Any]:
    content = prepared.body.content if hasattr(prepared.body, "content") else b""
    return ResponseContext(
        response,
        AttemptIdentity(attempt),
        PreparedRequestSummary(
            prepared.method.decode("ascii"), prepared.target.decode("ascii"), len(content)
        ),
        OperationInfo(operation_id),
        models,
    )


async def _maybe_await(value: object) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def envelope[T, TRaw](result: ExecutionResult[T]) -> ResponseEnvelope[T, TRaw]:
    return ResponseEnvelope(result.value, cast(NormalizedResponse[TRaw], result.response))


def _has_future_expiry(value: object) -> bool:
    return _not_expired(value) and isinstance(getattr(value, "expires_at", None), datetime)


def _not_expired(value: object) -> bool:
    expires = getattr(value, "expires_at", None)
    if not isinstance(expires, datetime):
        return False
    now = datetime.now(UTC)
    if expires.tzinfo is None:
        now = now.replace(tzinfo=None)
    return expires > now


__all__ = ["ExecutionCore", "ExecutionResult", "ExecutionRuntime", "envelope"]
