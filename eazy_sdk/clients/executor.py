"""The single logical-call/attempt coordinator used by both public clients."""

from __future__ import annotations

import inspect
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, cast, get_type_hints
from urllib.parse import urljoin, urlsplit

from eazy_sdk.auth.core import (
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
    TransportRequirement,
    TransportRequirements,
    ValuePatch,
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
    freeze_value,
    thaw_value,
)
from eazy_sdk.crypto._compiler import (
    CompiledPayloadCrypto,
    compile_payload_crypto,
    validate_crypto_runtime,
)
from eazy_sdk.crypto._http import (
    prepare_http_document,
    protect_http_request,
    unprotect_http_response,
)
from eazy_sdk.crypto._inputs import resolve_crypto_inputs
from eazy_sdk.dependencies import (
    RequestDependency,
    _DependencyCaches,
    _lower_requirements,
    _resolve_requirements,
)
from eazy_sdk.driver import sleep
from eazy_sdk.handlers import EmitOptions, HandlerProfile, TransportError, validate_profile
from eazy_sdk.identity import _IdentityScope
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
from eazy_sdk.protocols import CorrelationKey
from eazy_sdk.ratelimit_runtime import RateLimitContext, RateLimiter
from eazy_sdk.request.descriptors import JsonBody, ReplayableStreamBody
from eazy_sdk.request.logical import ExactBodyInput, NoBodyInput
from eazy_sdk.request.pipeline import REQUEST_PIPELINE, RequestStage
from eazy_sdk.request.prepared import (
    _NO_BODY_DOCUMENT_OVERRIDE,
    BufferedBody,
    HeaderField,
    HttpProtocol,
    PreparedRequest,
    RequestPreparer,
    json_body_document,
)
from eazy_sdk.request.signatures import reserve_outputs, sign_prepared
from eazy_sdk.response import (
    NormalizedResponse,
    ResponseContext,
    ResponseEnvelope,
    Responses,
)
from eazy_sdk.response._mapping import error_cases
from eazy_sdk.response.cases import (
    AttemptIdentity,
    OperationInfo,
    PreparedRequestSummary,
    PreparedResponseExtractor,
    Success,
)
from eazy_sdk.serialization import BackendCapabilityError, Serialization

from ._decisions import (
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
from .attempts import (
    AttemptBudgets,
    AttemptState,
    AuthRefreshPolicy,
    Continue,
    ProtectionPolicy,
    RedirectPolicy,
    ResponseRetryPolicy,
    Stop,
    TransportFailure,
    TransportRetryPolicy,
)
from .attempts import Fail as AttemptFail

_operation_stack: ContextVar[tuple[str, ...]] = ContextVar("eazy_sdk_operation_stack", default=())


@contextmanager
def _operation_frame(operation_id: str) -> Iterator[None]:
    """Guard one operation against re-entering itself through its own auth lifecycle."""

    stack = _operation_stack.get()
    if operation_id in stack:
        from eazy_sdk.auth.session_runtime import ResolutionCycleError

        raise ResolutionCycleError("operation cycle: " + " -> ".join((*stack, operation_id)))
    token = _operation_stack.set((*stack, operation_id))
    try:
        yield
    finally:
        _operation_stack.reset(token)


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
    serialization: Serialization

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
            protocol=HttpProtocol.HTTP_1_1,
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
    serialization: Serialization,
) -> SolveContext:
    emit_options = options.emit_options()
    fetch: ProtectedFetch = _RuntimeFetch(runtime, emit_options, identity, serialization)
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
                await sleep(delay)
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
    operation_protections: tuple[ProtectionFlow[Any], ...] = ()
    before_call_policies: tuple[BeforeCallPolicy[Any, Any], ...] = ()
    challenge_policies: tuple[ChallengePolicy[Any, Any], ...] = ()
    solver_bindings: SolverBindings = field(default_factory=SolverBindings)
    protection_session_owner: object | None = None
    middleware: tuple[object, ...] = ()
    limiter: RateLimiter | None = None
    crypto: CryptoRegistry = field(default_factory=CryptoRegistry)
    errors: Mapping[str, Any] = field(default_factory=dict)
    """Host-scoped error cases from ``ClientConfig.errors``, keyed by exact host."""
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
    identity: _IdentityScope,
    options: Any,
) -> tuple[str, ...]:
    scope = _scope_context(contract, _service_base_url(contract, runtime))
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


def transport_requirements(contract: _OperationDeclaration[Any]) -> TransportRequirements:
    dimensions: list[TransportRequirement] = []
    wire = contract.wire
    if wire.transport is not None:
        dimensions.append(TransportRequirement("protocol", wire.transport))
    if wire.is_exact:
        dimensions.extend(
            TransportRequirement(name, "CAPTURE_VERIFIED")
            for name in (
                "exact_target",
                "header_order",
                "header_casing",
                "preencoded_body",
                "manual_cookie_field",
            )
        )
    if any(isinstance(field.placement, ReplayableStreamBody) for field in contract.input_fields):
        dimensions.append(TransportRequirement("replayable_streams", "BEST_EFFORT"))
    return TransportRequirements(tuple(dimensions))


class ExecutionCore:
    """Owns ordering, arbitration and all attempt budgets."""

    def __init__(
        self,
        runtime: ExecutionRuntime,
        *,
        identity: _IdentityScope | None = None,
        serialization: Serialization | None = None,
        resolution_graph: LifecycleGraph | None = None,
    ) -> None:
        self.runtime = runtime
        self.identity = identity if identity is not None else _IdentityScope()
        self.serialization = serialization if serialization is not None else Serialization()
        self.resolution_graph = resolution_graph
        self._client_error_contracts: dict[tuple[int, str], Any] = {}

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
        identity = self.identity
        if not options.resolve_managed:
            requirements = _managed_preparation_requirements(
                call.declaration,
                runtime,
                identity,
                call_options,
            )
            if requirements:
                raise PreparationIncompleteError(requirements)
            runtime = replace(
                runtime,
                operation_protections=(),
                before_call_policies=(),
                challenge_policies=(),
                solver_bindings=SolverBindings(),
                middleware=(),
                limiter=None,
                crypto=CryptoRegistry(),
            )
            identity = _IdentityScope()

        def stop(request: object, *, options: object) -> None:
            from eazy_sdk.request.prepared import PreparedRequest

            if not isinstance(request, PreparedRequest):
                raise TypeError("preparation boundary received an invalid request")
            raise _PreparedRequestCaptured(request)

        runtime = replace(runtime, send=stop)
        core = ExecutionCore(
            runtime,
            identity=replace(identity, observer=None),
            serialization=self.serialization,
            resolution_graph=self.resolution_graph,
        )
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
        compiled, initial_crypto, before_policies, challenge_policies = self._compile_call(contract)
        initial_compiled_crypto = _compile_http_crypto(
            compiled,
            initial_crypto,
            self.serialization.models,
            allow_async=self.runtime.allow_async_crypto,
        )
        mandatory = self._preflight(contract, compiled, before_policies, challenge_policies)
        registrations = (*self.runtime.middleware, *selected.middleware)

        async def terminal(current: CallMiddlewareContext[T]) -> ExecutionResult[T]:
            rebound = bind_plan(cast(Any, compiled.plan), current.arguments)
            with _operation_frame(compiled.contract.operation_id):
                return await self._attempts(
                    compiled,
                    rebound,
                    selected,
                    registrations,
                    before_policies,
                    challenge_policies,
                    mandatory,
                    contract,
                    initial_crypto,
                    initial_compiled_crypto,
                )

        context = CallMiddlewareContext[T](
            compiled.plan.operation, _normalize_arguments(compiled, call.arguments)
        )
        return await self._through_middleware(contract, registrations, context, terminal)

    def _compile_call[T](
        self, contract: _OperationDeclaration[T]
    ) -> tuple[
        Any,
        tuple[PayloadCrypto, HttpEncrypted] | None,
        tuple[_CompiledBeforeCallPolicy, ...],
        tuple[_CompiledChallengePolicy, ...],
    ]:
        initial_url = _contract_url(_service_base_url(contract, self.runtime), contract.path)
        initial_crypto = _resolve_http_crypto(contract, self.runtime.crypto, initial_url)
        contract = self._with_client_errors(contract, initial_url)
        scope_context = _scope_context(contract, _service_base_url(contract, self.runtime))
        before_policies = tuple(
            _compile_before_call_policy(policy)
            for policy in self.runtime.before_call_policies
            if policy.scope.matches(scope_context)
        )
        challenge_policies = tuple(
            _compile_challenge_policy(policy)
            for policy in self.runtime.challenge_policies
            if policy.scope.matches(scope_context)
        )
        declared = (
            replace(
                contract,
                crypto=initial_crypto[0],
                wire=replace(contract.wire, encrypted=initial_crypto[1]),
            )
            if initial_crypto is not None
            else contract
        )
        try:
            compiled: Any = compile_endpoint(
                declared,
                scope=declared.scope,
                requirements=transport_requirements(declared),
                fingerprint_context=(
                    *self.serialization.models.fingerprint_components(),
                    *_protection_fingerprint_components(before_policies, challenge_policies),
                ),
                private_bindings=(
                    tuple(item.apply for item in before_policies)
                    + tuple(item.apply for item in challenge_policies)
                ),
            )
        except PlanError as exc:
            if "private" not in str(exc):
                raise
            raise ProtectionConfigurationError(str(exc)) from exc
        return compiled, initial_crypto, before_policies, challenge_policies

    def _with_client_errors[T](
        self, contract: _OperationDeclaration[T], url: str
    ) -> _OperationDeclaration[T]:
        """The outermost error layer: what a host answers with, whichever SDK is speaking.

        The cases are appended behind the operation's own and its service's, carrying
        ``precedence=2`` so a tie is decided by the layer nearest the operation.
        """

        declared = self.runtime.errors
        if not declared or not isinstance(contract.responses, Responses):
            return contract
        host = (urlsplit(url).hostname or "").lower()
        entry = declared.get(host)
        if entry is None:
            return contract
        key = (id(contract), host)
        cached = self._client_error_contracts.get(key)
        if cached is not None:
            return cast(_OperationDeclaration[T], cached)
        responses = cast(Responses[T], contract.responses)
        host_errors = tuple(
            replace(case, precedence=2)
            for case in error_cases(
                entry, models=self.serialization.models, operation_id=contract.operation_id
            )
        )
        extended = replace(
            contract,
            responses=Responses(
                success=cast(tuple[Success[T], ...], responses.success),
                errors=(*responses.errors, *host_errors),
                fallback=responses.fallback,
            ),
        )
        self._client_error_contracts[key] = extended
        return extended

    def _preflight[T](
        self,
        contract: _OperationDeclaration[T],
        compiled: Any,
        before_policies: tuple[_CompiledBeforeCallPolicy, ...],
        challenge_policies: tuple[_CompiledChallengePolicy, ...],
    ) -> _MandatoryPreparation | None:
        """Capability and solver checks precede binding side effects and every provider."""

        validate_profile(compiled.plan.requirements, self.runtime.handler_profile)
        _validate_serialization(contract, self.serialization)
        mandatory = _validate_mandatory_protections(
            contract,
            compiled,
            self.runtime.operation_protections,
            self.runtime.solver_bindings,
            self.serialization.models,
        )
        requirements = (
            *(policy.solver for policy in challenge_policies),
            *(policy.solver for policy in before_policies if policy.solver is not None),
        )
        for requirement in requirements:
            if self.runtime.solver_bindings.get(requirement) is None:
                raise MissingSolverError(f"missing solver: {requirement.name}")
        return mandatory

    async def _through_middleware[T](
        self,
        contract: _OperationDeclaration[T],
        registrations: tuple[object, ...],
        context: CallMiddlewareContext[T],
        terminal: Callable[[CallMiddlewareContext[T]], Awaitable[ExecutionResult[T]]],
    ) -> ExecutionResult[T]:
        scope = _scope_context(contract, _service_base_url(contract, self.runtime))
        chain = tuple(
            item
            for item in registrations
            if isinstance(item, CallMiddlewareRegistration) and item.scope.matches(scope)
        )
        callback: Callable[[CallMiddlewareContext[T]], Awaitable[Any]] = terminal
        for registration in reversed(chain):
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
        run: _AttemptRun[T] = _AttemptRun(
            self,
            compiled,
            bound,
            options,
            registrations,
            before_call_policies,
            challenge_policies,
            contract,
            initial_crypto,
            initial_compiled_crypto,
        )
        return await run.execute(mandatory)

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
        key = _protection_cache_key(self.runtime, policy, solver, policy.challenge)
        acquire = self._before_call_acquirer(
            policy, compiled, values, options, attempt, identity, solver
        )
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

    def _before_call_acquirer(
        self,
        policy: _CompiledBeforeCallPolicy,
        compiled: Any,
        values: OperationValues,
        options: Any,
        attempt: int,
        identity: TransportIdentity,
        solver: ChallengeSolver[Any, Any] | None,
    ) -> Callable[[], Awaitable[object]]:
        """Either the declared acquire operation, or the policy's own solver."""

        async def acquire() -> object:
            if policy.acquire is not None:
                acquired = await self.execute(policy.acquire.call({}), options=options)
                return acquired.value
            assert solver is not None and policy.challenge is not None
            try:
                return await solver.solve(
                    policy.challenge,
                    _solve_context(
                        self.runtime,
                        options,
                        compiled.plan.operation,
                        None,
                        attempt,
                        identity,
                        _slot_headers(compiled, values),
                        self.serialization,
                    ),
                )
            except Exception as exc:
                raise ChallengeSolveError(policy.identity, attempt) from exc

        return acquire

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
            self.serialization,
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
                            self.serialization,
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
        if self.identity.observer is not None:
            self.identity.observer(phase, value)


@dataclass(slots=True)
class _PreparedAttempt:
    """Everything one prepared attempt carries into its response phase."""

    request: Any
    values: OperationValues
    auth_executions: tuple[Any, ...]
    scope: Any
    middleware: tuple[Any, ...]
    identity: TransportIdentity
    selected_crypto: tuple[PayloadCrypto, HttpEncrypted] | None
    compiled_crypto: CompiledPayloadCrypto | None
    crypto_values: CryptoValues
    crypto_aad: tuple[tuple[str, FrozenValue], ...]


class _AttemptRun[T]:
    """One logical call: its per-call caches, and the attempts the policies allow.

    ``execute`` is the coordinator: prepare, send, classify, ask the owning policy. Every
    attempt is prepared and signed again from the call's values, so invariant 10 holds by
    construction rather than by care.
    """

    def __init__(
        self,
        core: ExecutionCore,
        compiled: Any,
        bound: OperationValues,
        options: Any,
        registrations: tuple[object, ...],
        before_call_policies: tuple[_CompiledBeforeCallPolicy, ...],
        challenge_policies: tuple[_CompiledChallengePolicy, ...],
        contract: _OperationDeclaration[T],
        initial_crypto: tuple[PayloadCrypto, HttpEncrypted] | None,
        initial_compiled_crypto: CompiledPayloadCrypto | None,
    ) -> None:
        self.core = core
        self.compiled = compiled
        self.values = bound
        self.options = options
        self.registrations = registrations
        self.before_call_policies = before_call_policies
        self.challenge_policies = challenge_policies
        self.contract = contract
        self.initial_crypto = initial_crypto
        self.initial_compiled_crypto = initial_compiled_crypto
        self.dependencies = _DependencyCaches()
        self.call_states: dict[str, _ManagedProtectionState] = {}
        self.applied_shared: dict[str, tuple[_ProtectionCacheKey, _ManagedProtectionState]] = {}
        self.mandatory_results: dict[int, object] = {}
        self.injected: dict[RequestDependency[Any], object] = {}
        """The ``requires=`` values of the current attempt, handed to a two-argument projection."""
        self.transport_retry = TransportRetryPolicy()
        self.response_retry = ResponseRetryPolicy()
        self.redirect = RedirectPolicy()
        self.auth_refresh = AuthRefreshPolicy()
        self.protection = ProtectionPolicy()

    async def execute(self, mandatory: _MandatoryPreparation | None) -> ExecutionResult[T]:
        if mandatory is not None:
            self.mandatory_results = await self.core._acquire_mandatory_protections(
                self.compiled, self.values, self.options, mandatory
            )
        state = AttemptState.initial(
            self._initial_url(), self._budgets(), correlation=self._new_correlation()
        )
        while state.number <= state.budgets.hard_limit:
            self.core._observe("start_attempt", self._trace(state))
            self.dependencies.attempt.clear()
            attempt = await self._prepare(state)
            decision = await self._attempt(state, attempt)
            if isinstance(decision, Stop):
                return decision.result
            if isinstance(decision, AttemptFail):
                raise decision.error
            state = await self._commit(decision, attempt)
        from eazy_sdk.clients.base import AttemptLimitError

        raise AttemptLimitError("hard attempt budget exhausted")

    def _initial_url(self) -> str:
        return _contract_url(
            _service_base_url(self.compiled.contract, self.core.runtime),
            self.compiled.contract.path,
        )

    def _budgets(self) -> AttemptBudgets:
        return AttemptBudgets.of(
            max_attempts=self.options.max_attempts,
            transport_retries=self.options.transport_retries,
            auth_retries=self.options.auth_retries,
            max_redirects=self.options.max_redirects,
            replays={item.identity: item.replay.max_replays for item in self.challenge_policies},
        )

    @staticmethod
    def _trace(state: AttemptState) -> dict[str, object]:
        budgets = state.budgets
        return {
            "number": state.number,
            "kind": state.kind,
            "reason": state.reason,
            "budgets": {
                "transport": budgets.transport,
                "auth": budgets.auth,
                "redirect": budgets.redirect,
                "replays": dict(budgets.replays),
            },
        }

    async def _commit(self, decision: Continue, attempt: _PreparedAttempt) -> AttemptState:
        """Run the one effect the deciding policy asked for, then adopt its state."""

        if decision.patch is not None:
            self.values = apply_patch_atomic(self.values, decision.patch)
        if decision.wait_attempt is not None:
            await self.options.retry.wait(decision.wait_attempt)
        if decision.refresh_auth:
            await _refresh_security(
                attempt.auth_executions, self.core.identity.auth, self.core.resolution_graph
            )
        if decision.reaction is not None:
            await self._solve(decision.reaction, decision.state, attempt)
        return self._recorrelate(decision.state)

    def _new_correlation(self) -> str | None:
        """A fresh envelope correlation, when the operation's service speaks an envelope."""

        envelope = self.contract.envelope
        if envelope is None:
            return None
        generator = getattr(envelope, "new_correlation", None)
        return None if generator is None else cast(str, generator())

    def _recorrelate(self, state: AttemptState) -> AttemptState:
        """Reuse the correlation on a repeat, unless the protocol asks for a new one.

        Reusing it is the default because a repeat of the same call should be recognisable to
        the server as a duplicate, the way an idempotency key is; a service that rejects a
        repeated id declares ``id_on_retry="regenerate"`` and gets a fresh one per attempt.
        """

        envelope = self.contract.envelope
        if envelope is None or getattr(envelope, "id_on_retry", "reuse") != "regenerate":
            return state
        return replace(state, correlation=self._new_correlation())

    async def _prepare(self, state: AttemptState) -> _PreparedAttempt:
        selected = _resolve_http_crypto(self.contract, self.core.runtime.crypto, state.url)
        compiled_crypto = (
            self.initial_compiled_crypto
            if selected == self.initial_crypto
            else _compile_http_crypto(
                self.compiled,
                selected,
                self.core.serialization.models,
                allow_async=self.core.runtime.allow_async_crypto,
            )
        )
        values, auth_executions, crypto_values, crypto_aad = await self._resolved_values(
            state, compiled_crypto
        )
        scope = _scope_context(
            self.compiled.contract,
            _service_base_url(self.compiled.contract, self.core.runtime),
            state.url,
        )
        middleware = tuple(
            item
            for item in self.registrations
            if isinstance(item, AttemptMiddlewareRegistration) and item.scope.matches(scope)
        )
        values = await self._contributed(values, middleware, state)
        # Managed protection state is applied only after every public write of this attempt
        # is known, so the transport identity it is checked against (User-Agent, proxy,
        # impersonation) is the one the request will carry.
        identity = _transport_identity(self.core.runtime, _slot_headers(self.compiled, values))
        values = await self._managed_values(state, values, identity)
        await self._rate_limit(state)
        request = await self._build_request(
            state, values, selected, compiled_crypto, crypto_values, crypto_aad
        )
        attempt = _PreparedAttempt(
            request,
            values,
            auth_executions,
            scope,
            middleware,
            identity,
            selected,
            compiled_crypto,
            crypto_values,
            crypto_aad,
        )
        self._before_emit(state, attempt)
        return attempt

    async def _resolved_values(
        self, state: AttemptState, compiled_crypto: CompiledPayloadCrypto | None
    ) -> tuple[OperationValues, tuple[Any, ...], CryptoValues, tuple[tuple[str, FrozenValue], ...]]:
        contract = self.compiled.contract
        self.injected = {}
        dependency_patch = await _resolve_requirements(
            _lower_requirements(
                (*contract.requires, *contract.inject),
                self.compiled,
                self.core.identity.dependencies,
            ),
            self.core.identity.dependencies,
            operation_id=contract.operation_id,
            attempt=state.number,
            caches=self.dependencies,
            resolved=self.injected,
        )
        crypto_values = CryptoValues()
        crypto_aad: tuple[tuple[str, FrozenValue], ...] = ()
        if compiled_crypto is not None and compiled_crypto.profile.inputs:
            crypto_values, crypto_aad = await resolve_crypto_inputs(
                compiled_crypto.profile.inputs,
                self.core.identity.dependencies,
                operation_id=contract.operation_id,
                attempt=state.number,
            )
        auth_executions, auth_patch = await resolve_security(
            contract.security,
            self.core.identity.auth,
            cast(Any, self.compiled),
            graph=self.core.resolution_graph,
        )
        values = apply_patch_atomic(
            self.values,
            ValuePatch((*dependency_patch.operations, *auth_patch.operations)),
        )
        return values, auth_executions, crypto_values, crypto_aad

    async def _contributed(
        self, values: OperationValues, middleware: tuple[Any, ...], state: AttemptState
    ) -> OperationValues:
        operations: list[Any] = []
        for registration in middleware:
            contribute = getattr(registration.implementation, "contribute", None)
            if contribute is None:
                continue
            patch = await _maybe_await(
                contribute(AttemptRequestContext(self.compiled.plan.operation, state.number))
            )
            if patch is not None:
                operations.extend(patch.operations)
        if not operations:
            return values
        return apply_patch_atomic(values, ValuePatch(tuple(operations)))

    async def _managed_values(
        self, state: AttemptState, values: OperationValues, identity: TransportIdentity
    ) -> OperationValues:
        for policy in self.before_call_policies:
            managed, shared = await self.core._before_call_state(
                policy,
                self.compiled,
                values,
                self.options,
                state.number,
                self.call_states,
                identity,
            )
            values = _apply_managed_state(
                self.compiled, values, policy.apply, managed, policy=policy.identity
            )
            if shared is not None:
                self.applied_shared[policy.identity] = shared
        fingerprint = identity.fingerprint()
        for challenge in self.challenge_policies:
            local = self.call_states.get(challenge.identity)
            if local is not None and not _identity_matches(local, fingerprint):
                self.call_states.pop(challenge.identity, None)
                local = None
            shared = _find_shared_state(self.core.runtime, challenge, fingerprint)
            selected = local or (shared[1] if shared is not None else None)
            if selected is None:
                continue
            values = _apply_managed_state(
                self.compiled, values, challenge.apply, selected, policy=challenge.identity
            )
            if shared is not None and (local is None or local is shared[1]):
                self.applied_shared[challenge.identity] = shared
            if challenge.persistence.mode in {
                ProtectionPersistenceMode.PER_MATCH,
                ProtectionPersistenceMode.PER_ATTEMPT,
            }:
                self.call_states.pop(challenge.identity, None)
        return values

    async def _rate_limit(self, state: AttemptState) -> None:
        limiter = self.core.runtime.limiter
        if limiter is None:
            return
        decision = await _maybe_await(
            limiter.reserve(
                RateLimitContext(
                    self.compiled.plan.operation,
                    self.compiled.contract.method,
                    urlsplit(state.url).netloc,
                    state.number,
                    state.kind,
                )
            )
        )
        if decision.delay > 0:
            await sleep(decision.delay)
        self.core._observe("rate_limit", state.number)

    def _crypto_context(
        self,
        state: AttemptState,
        selected: tuple[PayloadCrypto, HttpEncrypted] | None,
        compiled_crypto: CompiledPayloadCrypto,
        direction: CryptoDirection,
        stage: CryptoStage,
        crypto_values: CryptoValues,
        crypto_aad: tuple[tuple[str, FrozenValue], ...],
        **extra: Any,
    ) -> Any:
        return _http_crypto_context(
            compiled_crypto,
            selected,
            self.compiled.contract.operation_id,
            state.effective_method(self.compiled.contract.method),
            state.url,
            state.number,
            direction,
            stage,
            values=crypto_values,
            aad=crypto_aad,
            **extra,
        )

    async def _build_request(
        self,
        state: AttemptState,
        values: OperationValues,
        selected: tuple[PayloadCrypto, HttpEncrypted] | None,
        compiled_crypto: CompiledPayloadCrypto | None,
        crypto_values: CryptoValues,
        crypto_aad: tuple[tuple[str, FrozenValue], ...],
    ) -> Any:
        """Run the declared pipeline; the order lives in ``REQUEST_PIPELINE``, not here."""

        build = _RequestBuild(
            self, state, values, selected, compiled_crypto, crypto_values, crypto_aad
        )
        for stage in REQUEST_PIPELINE:
            await build.run(stage)
        prepared = build.prepared
        self.core._observe("stages", tuple(build.executed))
        self.core._observe(
            "prepared",
            PreparedRequestSummary(
                prepared.method.decode("ascii"),
                prepared.target.partition(b"?")[0].decode("ascii"),
                len(prepared.body.content) if hasattr(prepared.body, "content") else 0,
            ),
        )
        return prepared

    @staticmethod
    def _encodes_body(
        selected: tuple[PayloadCrypto, HttpEncrypted] | None,
        compiled_crypto: CompiledPayloadCrypto | None,
        state: AttemptState,
    ) -> bool:
        if compiled_crypto is None or compiled_crypto.profile.outbound is None or state.omit_body:
            return False
        return compiled_crypto.profile.outbound.encoded is not None or (
            selected is not None and bool(selected[1].metadata)
        )

    def _sign(self, unsigned: Any, signature_plan: Any) -> Any:
        if not signature_plan.signatures:
            return unsigned.finalize()
        if self.core.identity.key_provider is None:
            raise ValueError("signing key provider is not configured")
        prepared = sign_prepared(unsigned, signature_plan, self.core.identity.key_provider)
        if not isinstance(prepared.body, BufferedBody):
            return prepared
        media_type = (
            prepared.body.content_type.decode("ascii")
            if prepared.body.content_type is not None
            else None
        )
        return replace(prepared, body_input=ExactBodyInput(prepared.body.content, media_type))

    def _before_emit(self, state: AttemptState, attempt: _PreparedAttempt) -> None:
        for registration in attempt.middleware:
            before_emit = getattr(registration.implementation, "before_emit", None)
            if before_emit is None:
                continue
            decision = before_emit(
                PreparedAttemptContext(
                    self.compiled.plan.operation, state.number, attempt.request
                )
            )
            if isinstance(decision, Fail):
                raise decision.error

    async def _attempt(
        self, state: AttemptState, attempt: _PreparedAttempt
    ) -> Continue | Stop[ExecutionResult[T]] | AttemptFail:
        try:
            response = cast(
                NormalizedResponse[Any],
                await _maybe_await(
                    self.core.runtime.send(attempt.request, options=self.options.emit_options())
                ),
            )
            self.core._observe("emit", state.number)
        except TransportError as error:
            failure = await self._transport_failure(state, attempt, error)
            return self.transport_retry.decide(state, failure)
        return await self._respond(state, attempt, response)

    async def _transport_failure(
        self, state: AttemptState, attempt: _PreparedAttempt, error: TransportError
    ) -> TransportFailure:
        proposed: object | None = None
        for registration in attempt.middleware:
            hook = getattr(registration.implementation, "on_transport_error", None)
            if hook is None:
                continue
            decision = await _maybe_await(
                hook(
                    AttemptTransportErrorContext(
                        self.compiled.plan.operation, state.number, error
                    )
                )
            )
            if isinstance(decision, Fail):
                raise decision.error from None
            if isinstance(decision, ProposeAction):
                proposed = decision.action
        return TransportFailure(
            error,
            proposed,
            idempotent=self.compiled.contract.is_idempotent,
            retries_configured=bool(self.options.retry.retries),
        )

    async def _respond(
        self, state: AttemptState, attempt: _PreparedAttempt, response: NormalizedResponse[Any]
    ) -> Continue | Stop[ExecutionResult[T]] | AttemptFail:
        if attempt.compiled_crypto is not None and attempt.compiled_crypto.profile.inbound:
            assert attempt.selected_crypto is not None
            response = await unprotect_http_response(
                response,
                attempt.compiled_crypto,
                attempt.selected_crypto[1],
                context=self._crypto_context(
                    state,
                    attempt.selected_crypto,
                    attempt.compiled_crypto,
                    CryptoDirection.INBOUND,
                    CryptoStage.ENCODED,
                    attempt.crypto_values,
                    attempt.crypto_aad,
                    clear_content_type=attempt.selected_crypto[1].clear_content_type,
                    outer_content_type=response.content_type,
                ),
            )
        context = self._response_context(state, attempt, response)
        response, context, proposed = await self._after_response(state, attempt, response, context)
        signal = self._signal(state, context, attempt.scope)
        decision = decide_response(
            ResponseDecisionInput(
                response=cast(NormalizedResponse[object], response),
                proposed=proposed,
                signal=signal,
                outcome=(
                    self.compiled.contract.responses.inspect(context)
                    if isinstance(self.compiled.contract.responses, Responses)
                    else None
                ),
                idempotent=self.compiled.contract.is_idempotent,
                attempt=state.number,
                hard_attempt_limit=state.budgets.hard_limit,
                transport_remaining=state.budgets.transport,
                retry_statuses=self.options.retry.retry_statuses,
                redirect_remaining=state.budgets.redirect,
                auth_remaining=state.budgets.auth,
                auth_refreshable=_has_refreshable_security(
                    attempt.auth_executions, self.core.identity.auth
                ),
                current_url=state.url,
                effective_method=state.effective_method(self.compiled.contract.method),
                raw_response=self.compiled.contract.raw_response,
            )
        )
        return self._route(state, attempt, decision, context)

    def _response_context(
        self, state: AttemptState, attempt: _PreparedAttempt, response: NormalizedResponse[Any]
    ) -> Any:
        return _response_context(
            response,
            attempt.request,
            self.compiled.contract.operation_id,
            state.number,
            serialization=self.core.serialization,
            correlation=state.correlation,
        )

    async def _after_response(
        self,
        state: AttemptState,
        attempt: _PreparedAttempt,
        response: NormalizedResponse[Any],
        context: Any,
    ) -> tuple[NormalizedResponse[Any], Any, object | None]:
        proposed: object | None = None
        for registration in attempt.middleware:
            after = getattr(registration.implementation, "after_response", None)
            if after is None:
                continue
            decision = await _maybe_await(
                after(
                    AttemptResponseContext(
                        self.compiled.plan.operation,
                        state.number,
                        cast(ResponseContext[object], context),
                    )
                )
            )
            if isinstance(decision, Fail):
                raise decision.error
            if isinstance(decision, ReplaceResponse):
                response = decision.response
                context = self._response_context(state, attempt, response)
            if isinstance(decision, ProposeAction):
                proposed = decision.action
        return response, context, proposed

    def _signal(self, state: AttemptState, context: Any, scope: Any) -> Any:
        signal = _inspect_signals(
            cast(Any, tuple(policy.signal for policy in self.challenge_policies)),
            cast(ResponseContext[object], context),
            scope,
        )
        if isinstance(signal, MalformedSignal):
            raise ChallengeParseError(
                _policy_identity(self.challenge_policies, signal.signal), state.number
            ) from signal.cause
        if isinstance(signal, AmbiguousSignal):
            raise AmbiguousChallengeError(
                tuple(_policy_identity(self.challenge_policies, item) for item in signal.signals),
                state.number,
            )
        return signal

    def _route(
        self, state: AttemptState, attempt: _PreparedAttempt, decision: Any, context: Any
    ) -> Continue | Stop[ExecutionResult[T]] | AttemptFail:
        if isinstance(decision, RetryTransition):
            return self.response_retry.decide(
                state,
                decision.kind,
                patch=decision.patch,
                consumes_transport=decision.consumes_transport,
            )
        if isinstance(decision, RedirectTransition):
            return self.redirect.decide(state, decision.url, decision.method, decision.omit_body)
        if isinstance(decision, ReactionTransition):
            return self._reaction(state, attempt, decision.match, context)
        if isinstance(decision, AuthRefreshTransition):
            return self.auth_refresh.decide(state)
        if isinstance(decision, TerminalResponse):
            return Stop(
                ExecutionResult(
                    cast(T, decision.value), cast(NormalizedResponse[Any], decision.response)
                )
            )
        assert isinstance(decision, RejectedResponse)
        decision.outcome.unwrap()
        raise AssertionError("terminal response outcome unexpectedly returned")

    def _reaction(
        self, state: AttemptState, attempt: _PreparedAttempt, match: Any, context: Any
    ) -> Continue | Stop[ExecutionResult[T]] | AttemptFail:
        policy = next(item for item in self.challenge_policies if item.signal is match.signal)
        _ensure_replay_allowed(
            cast(Any, self.compiled),
            attempt.values,
            attempt.request.body,
            policy.replay,
            origin_may_have_executed=True,
            remaining=state.budgets.replays[policy.identity],
        )
        self.pending_context = context
        return self.protection.decide(state, policy.identity, match)

    async def _solve(self, match: Any, state: AttemptState, attempt: _PreparedAttempt) -> None:
        policy = next(item for item in self.challenge_policies if item.signal is match.signal)
        managed, shared = await self.core._challenge_state(
            policy,
            match.value,
            cast(ResponseContext[object], self.pending_context),
            self.compiled,
            attempt.values,
            self.options,
            state.number - 1,
            self.call_states,
            self.applied_shared.get(policy.identity),
            attempt.identity,
            _prepared_headers(attempt.request),
        )
        self.call_states[policy.identity] = managed
        if shared is not None:
            self.applied_shared[policy.identity] = shared


class _RequestBuild:
    """One attempt's walk through :data:`REQUEST_PIPELINE`.

    Each method knows how to run one stage and nothing about what comes before or after,
    so the order can only be changed in one place. A stage the operation did not declare
    is skipped rather than executed as a no-op, and ``executed`` records what really ran.
    """

    def __init__(
        self,
        run: _AttemptRun[Any],
        state: AttemptState,
        values: OperationValues,
        selected: tuple[PayloadCrypto, HttpEncrypted] | None,
        compiled_crypto: CompiledPayloadCrypto | None,
        crypto_values: CryptoValues,
        crypto_aad: tuple[tuple[str, FrozenValue], ...],
    ) -> None:
        self.run_context = run
        self.state = state
        self.values = values
        self.selected = selected
        self.crypto = compiled_crypto
        self.crypto_values = crypto_values
        self.crypto_aad = crypto_aad
        self.document: object = _NO_BODY_DOCUMENT_OVERRIDE
        self.outputs: list[CryptoOutputValue[object]] = []
        self.unsigned: Any = None
        self.prepared: Any = None
        self.executed: list[RequestStage] = []

    async def run(self, stage: RequestStage) -> None:
        if not self._declared(stage):
            return
        self.executed.append(stage)
        if stage is RequestStage.PROJECTION:
            self._project()
        elif stage is RequestStage.ENVELOPE:
            self._wrap()
        elif stage is RequestStage.DOCUMENT_CRYPTO:
            await self._encrypt_document()
        elif stage is RequestStage.ENCODE:
            self._encode()
        elif stage is RequestStage.ENCODED_CRYPTO:
            await self._encrypt_encoded()
        else:
            self._sign()

    def _declared(self, stage: RequestStage) -> bool:
        compiled = self.run_context.compiled
        if stage is RequestStage.PROJECTION:
            return compiled.body_projection is not None or bool(compiled.private_body_writers)
        if stage is RequestStage.ENVELOPE:
            return compiled.contract.envelope is not None
        if stage is RequestStage.DOCUMENT_CRYPTO:
            return (
                self.crypto is not None
                and bool(self.crypto.outbound_fields)
                and not self.state.omit_body
            )
        if stage is RequestStage.ENCODED_CRYPTO:
            return self.run_context._encodes_body(self.selected, self.crypto, self.state)
        return True

    def _project(self) -> None:
        run = self.run_context
        self.document = build_request_document(
            RequestDocumentStageInput(
                run.compiled,
                self.values,
                run.core.serialization.models,
                run.mandatory_results,
                run.injected,
            )
        ).document

    def _wrap(self) -> None:
        contract = self.run_context.compiled.contract
        envelope = contract.envelope
        assert envelope is not None and contract.discriminator is not None
        payload = self.document
        if payload is _NO_BODY_DOCUMENT_OVERRIDE:
            payload = json_body_document(
                self.run_context.compiled,
                self.values,
                models=self.run_context.core.serialization.models,
            )
        correlation = (
            CorrelationKey(self.state.correlation) if self.state.correlation else None
        )
        self.document = thaw_value(
            envelope.build_outbound(
                contract.discriminator, freeze_value(payload), correlation=correlation
            )
        )

    async def _encrypt_document(self) -> None:
        run = self.run_context
        crypto = self.crypto
        assert crypto is not None
        if self.document is _NO_BODY_DOCUMENT_OVERRIDE:
            body_slot = run.compiled.body_slot
            if body_slot is not None:
                self.document = run.core.serialization.models.dump(self.values.require(body_slot))
        if self.document is _NO_BODY_DOCUMENT_OVERRIDE:
            raise CryptoConfigurationError(
                "outbound field crypto requires a semantic JSON request body"
            )
        self.document = await prepare_http_document(
            self.document,
            crypto,
            context=run._crypto_context(
                self.state,
                self.selected,
                crypto,
                CryptoDirection.OUTBOUND,
                CryptoStage.DOCUMENT,
                self.crypto_values,
                self.crypto_aad,
            ),
            outputs=self.outputs,
        )

    def _encode(self) -> None:
        run = self.run_context
        state = self.state
        try:
            self.unsigned = RequestPreparer(
                _service_base_url(run.compiled.contract, run.core.runtime),
                run.core.serialization.models,
                run.core.serialization.json,
            ).prepare(
                run.compiled,
                self.values,
                reserved_outputs=reserve_outputs(run.compiled.signature_plan),
                url_override=state.url if state.redirected else None,
                method_override=state.method,
                omit_body=state.omit_body,
                body_document_override=self.document,
            )
        except BindingError:
            raise OperationBindingError(
                code="preparation_failed",
                operation_id=run.compiled.contract.operation_id,
                field=None,
                phase="prepare",
                detail="request values could not be prepared",
            ) from None

    async def _encrypt_encoded(self) -> None:
        run = self.run_context
        selected, crypto = self.selected, self.crypto
        assert selected is not None and crypto is not None
        self.unsigned = await protect_http_request(
            self.unsigned,
            crypto,
            selected[1],
            context=run._crypto_context(
                self.state,
                selected,
                crypto,
                CryptoDirection.OUTBOUND,
                CryptoStage.ENCODED,
                self.crypto_values,
                self.crypto_aad,
            ),
            outputs=self.outputs,
        )

    def _sign(self) -> None:
        self.prepared = self.run_context._sign(
            self.unsigned, self.run_context.compiled.signature_plan
        )


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


def _validate_serialization(
    contract: _OperationDeclaration[Any],
    serialization: Serialization,
) -> None:
    """Refuse an operation whose bytes the declared backends cannot produce or read.

    Both halves fail the same way if left to the runtime: a JSON backend that quietly
    encodes differently breaks a signature computed over the bytes, and a parser that does
    not speak the operation's selector language extracts nothing at all. Neither is worth
    discovering on the first call to a private API, so both are settled here.
    """

    policy = contract.wire.json_policy
    if not serialization.json.supports(policy):
        raise BackendCapabilityError(
            f"operation {contract.operation_id!r} requires JSON encoding {policy!r}, which the "
            f"{serialization.json.name!r} backend cannot produce"
        )
    responses = contract.responses
    for case in getattr(responses, "cases", ()):
        extractor = getattr(case.response, "extractor", None)
        model = getattr(case.response, "model", None)
        if not isinstance(extractor, PreparedResponseExtractor) or model is None:
            continue
        try:
            extractor.prepare(model, serialization)
        except BackendCapabilityError as exc:
            raise BackendCapabilityError(f"operation {contract.operation_id!r}: {exc}") from exc


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


def _service_base_url(contract: _OperationDeclaration[Any], runtime: ExecutionRuntime) -> str:
    """The router's declared address, falling back to the client that carries the bytes."""

    return contract.base_url or runtime.base_url


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
    wire: object = contract.wire.encrypted
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
        wire = resolved.encrypted
    if not isinstance(profile, PayloadCrypto):
        raise CryptoConfigurationError("HTTP operation requires a PayloadCrypto profile")
    if wire is None:
        selected_wire = HttpEncrypted()
    elif isinstance(wire, HttpEncrypted):
        selected_wire = wire
    else:
        raise CryptoConfigurationError("HTTP operation requires an HttpEncrypted wire binding")
    return profile, selected_wire



def _validate_crypto_body(compiled: Any, profile: PayloadCrypto) -> None:
    """The document crypto encrypts must be one semantic JSON body it can replay."""

    root_body = next((field for field in compiled.input_fields if field.is_root_body), None)
    projected_json = compiled.body_projection is not None and isinstance(
        compiled.body_projection.encoding, JsonBody
    )
    if profile.outbound is None:
        return
    if (
        profile.outbound.fields
        and not projected_json
        and (root_body is None or not isinstance(root_body.placement, JsonBody))
    ):
        raise CryptoConfigurationError(
            "outbound field crypto requires one semantic JsonBody document"
        )
    if profile.outbound.encoded is None:
        return
    conflicts = {
        name.casefold()
        for name in compiled.header_slots
        if name.casefold() in {"content-type", "content-length"}
    }
    if conflicts:
        raise CryptoConfigurationError(
            "crypto wire binding owns HTTP representation headers: " + ", ".join(sorted(conflicts))
        )
    if root_body is not None and isinstance(root_body.placement, ReplayableStreamBody):
        raise CryptoStreamingUnsupportedError(
            "whole-payload crypto does not support ReplayableStreamBody"
        )


def _validate_crypto_metadata(compiled: Any, profile: PayloadCrypto, wire: HttpEncrypted) -> None:
    """Crypto metadata owns its headers, and binds exactly the outputs it declares."""

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
    declared: list[object] = []
    if profile.outbound is not None:
        for item in profile.outbound.fields:
            declared.extend(item.outputs)
        if profile.outbound.encoded is not None:
            declared.extend(profile.outbound.encoded.outputs)
    if profile.inbound is not None:
        for inbound_field in profile.inbound.fields:
            declared.extend(inbound_field.metadata)
        if profile.inbound.encoded is not None:
            declared.extend(profile.inbound.encoded.metadata)
    if {id(item) for item in declared} != {id(item.output) for item in wire.metadata}:
        raise CryptoConfigurationError(
            "HTTP crypto metadata bindings must exactly match declared output writes and reads"
        )


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
    _validate_crypto_body(compiled, profile)
    _validate_crypto_metadata(compiled, profile, wire)
    root_body = next((field for field in compiled.input_fields if field.is_root_body), None)
    compiled_profile = compile_payload_crypto(
        profile,
        models,
        outbound_model=(
            compiled.body_projection.target
            if compiled.body_projection is not None
            else root_body.annotation if root_body is not None else None
        ),
        inbound_models=_http_inbound_models(compiled.contract),
        json=compiled.contract.wire.json_policy,
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
    serialization: Serialization,
    correlation: str | None = None,
) -> ResponseContext[Any]:
    content = prepared.body.content if hasattr(prepared.body, "content") else b""
    return ResponseContext(
        response,
        AttemptIdentity(attempt),
        PreparedRequestSummary(
            prepared.method.decode("ascii"), prepared.target.decode("ascii"), len(content)
        ),
        OperationInfo(operation_id, correlation),
        serialization,
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
