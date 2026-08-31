"""The single logical-call/attempt coordinator used by both public clients."""

from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast, get_type_hints
from urllib.parse import urljoin, urlsplit

from eazy_sdk._internal import (
    Bind,
    BoundArguments,
    OperationValues,
    ScopeContext,
    ValuePatch,
    WireRequirement,
    WireRequirements,
    WriterConflictError,
    apply_patch_atomic,
    bind_plan,
    compile_endpoint,
)
from eazy_sdk._internal.http_operation import _OperationCall, _OperationDeclaration
from eazy_sdk.accounts.session import LifecycleGraph
from eazy_sdk.auth.core import (
    AuthProviders,
    _has_refreshable_security,
    _refresh_security,
    resolve_security,
)
from eazy_sdk.crypto import (
    CryptoConfigurationError,
    CryptoDirection,
    CryptoInputScope,
    CryptoOutputValue,
    CryptoRegistry,
    CryptoStage,
    CryptoStreamingUnsupported,
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
from eazy_sdk.handlers import HandlerProfile, TransportFailure, validate_profile
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
    RedirectTo,
    ReplaceResponse,
    RetryAttempt,
    SingleUseNext,
)
from eazy_sdk.models import (
    ModelAdapterError,
    ModelAdapterRegistry,
    ModelDumpMode,
    default_model_adapters,
)
from eazy_sdk.protection import (
    MissingSolverError,
    ProtectionFlow,
    ReactionBudget,
    ReplayAction,
    SignalMatch,
    SolutionFreshness,
    SolveContext,
    SolverBindings,
    SolverRegistry,
    _validate_solution_target,
    inspect_signals,
    react,
    solution_patch,
)
from eazy_sdk.ratelimit_runtime import RateLimitContext, RateLimiter
from eazy_sdk.request import (
    BodyProjectionError,
    JsonBody,
    MultipartBody,
    ReplayableStreamBody,
    SigningKey,
    SigningKeyRequirement,
    WireProfile,
)
from eazy_sdk.request.logical import ExactBodyInput
from eazy_sdk.request.prepared import (
    _NO_BODY_DOCUMENT_OVERRIDE,
    BufferedBody,
    RequestPreparer,
)
from eazy_sdk.request.signatures import reserve_outputs, sign_prepared
from eazy_sdk.response import (
    NormalizedResponse,
    ResponseContext,
    ResponseEnvelope,
    Responses,
)
from eazy_sdk.response.cases import (
    AttemptIdentity,
    ErrorOutcome,
    OperationInfo,
    PreparedRequestSummary,
    SuccessOutcome,
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


@dataclass(slots=True)
class ExecutionRuntime:
    handler_profile: HandlerProfile
    send: Any
    base_url: str = ""
    dependencies: DependencyRegistry = field(default_factory=DependencyRegistry)
    auth: AuthProviders = field(default_factory=AuthProviders)
    solvers: SolverRegistry = field(default_factory=SolverRegistry)
    protection_solvers: SolverBindings = field(default_factory=SolverBindings)
    signals: tuple[object, ...] = ()
    protections: tuple[object, ...] = ()
    middleware: tuple[object, ...] = ()
    limiter: RateLimiter | None = None
    key_provider: KeyProvider | None = None
    observer: Observer | None = None
    models: ModelAdapterRegistry = field(default_factory=default_model_adapters)
    profile: WireProfile | None = None
    network_identity: object | None = None
    crypto: CryptoRegistry = field(default_factory=CryptoRegistry)
    allow_async_crypto: bool = True
    _solution_cache: dict[tuple[int, object | None], object] = field(
        default_factory=dict, init=False, repr=False
    )
    _solution_locks: dict[tuple[int, object | None], asyncio.Lock] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        for protection in self.protections:
            install = getattr(protection, "install", None)
            if callable(install):
                install(self.solvers)


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

    async def execute[T](
        self,
        call: _OperationCall[T],
        *,
        options: object,
    ) -> ExecutionResult[T]:
        from eazy_sdk.clients.base import CallOptions

        selected = cast(CallOptions, options)
        contract = call.declaration
        arguments = call.arguments
        initial_url = _contract_url(self.runtime.base_url, contract.path)
        initial_crypto = _resolve_http_crypto(contract, self.runtime.crypto, initial_url)
        compiled_contract = (
            replace(
                contract,
                crypto=initial_crypto[0],
                crypto_wire=initial_crypto[1],
            )
            if initial_crypto is not None
            else contract
        )
        compiled: Any = compile_endpoint(
            compiled_contract,
            scope=compiled_contract.scope,
            requirements=wire_requirements(compiled_contract),
            fingerprint_context=self.runtime.models.fingerprint_components(),
        )
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
            self.runtime.protections,
            self.runtime.protection_solvers,
            self.runtime.models,
        )
        scope_context = _scope_context(contract, self.runtime.base_url)
        protections = tuple(
            item
            for item in self.runtime.protections
            if getattr(item, "scope", contract.scope).matches(scope_context)
        )
        for protection in protections:
            requirement = getattr(protection, "solver_requirement", None)
            if requirement is not None and self.runtime.solvers.get(requirement) is None:
                from eazy_sdk.protection import MissingSolverError

                raise MissingSolverError(f"missing solver: {requirement.name}")
            for reaction in getattr(protection, "reactions", ()):
                _validate_solution_target(compiled, reaction.apply)
            for flow in getattr(protection, "before", ()):
                _validate_solution_target(compiled, flow.apply)
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
                    protections,
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
        protections: tuple[object, ...],
        mandatory: _MandatoryPreparation | None,
        contract: _OperationDeclaration[T],
        initial_crypto: tuple[PayloadCrypto, HttpEncrypted] | None,
        initial_compiled_crypto: CompiledPayloadCrypto | None,
    ) -> ExecutionResult[T]:
        values = bound
        mandatory_results = (
            await self._acquire_mandatory_protections(
                compiled,
                options,
                mandatory,
            )
            if mandatory is not None
            else {}
        )
        protection_before = tuple(
            flow for protection in protections for flow in getattr(protection, "before", ())
        )
        for flow in protection_before:
            acquire = flow.acquire
            if acquire is not None:
                if not isinstance(acquire, _OperationDeclaration):
                    candidate = getattr(acquire, "declaration", None)
                    if not isinstance(candidate, _OperationDeclaration):
                        raise TypeError("before-call acquire must reference a decorated operation")
                    acquire = candidate
                acquired = await self.execute(acquire.call({}), options=options)
                solution = acquired.value
            else:
                if flow.solver is None or flow.challenge is None:
                    raise TypeError("before-call flow requires acquire or challenge+solver")
                solver = self.runtime.solvers.get(flow.solver)
                if solver is None:
                    from eazy_sdk.protection import MissingSolverError

                    raise MissingSolverError(f"missing solver: {flow.solver.name}")
                solve_context = SolveContext(
                    compiled.plan.operation,
                    None,
                    0,
                    network_identity=cast(Any, self.runtime.network_identity),
                )
                if flow.freshness is SolutionFreshness.UNTIL_EXPIRY:
                    key = (id(flow), self.runtime.network_identity)
                    lock = self.runtime._solution_locks.setdefault(key, asyncio.Lock())
                    async with lock:
                        cached = self.runtime._solution_cache.get(key)
                        if cached is not None and _not_expired(cached):
                            solution = cached
                        else:
                            solution = await solver.solve(flow.challenge, solve_context)
                            if _has_future_expiry(solution):
                                self.runtime._solution_cache[key] = solution
                else:
                    solution = await solver.solve(flow.challenge, solve_context)
            patch = solution_patch(cast(Any, compiled), values, flow.apply, solution)
            values = apply_patch_atomic(values, patch)
        dependencies = _DependencyCaches()
        protection_reactions = tuple(
            reaction
            for protection in protections
            for reaction in getattr(protection, "reactions", ())
        )
        reaction_budget = ReactionBudget(
            sum(item.replay.max_replays for item in protection_reactions)
        )
        hard_attempt_limit = options.max_attempts + reaction_budget.remaining
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
            current_url = next_url or _contract_url(self.runtime.base_url, compiled.contract.path)
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
            body_document_override: object = (
                _project_body(
                    compiled,
                    attempt_values,
                    self.runtime.models,
                    mandatory_results,
                )
                if compiled.body_projection is not None
                else _NO_BODY_DOCUMENT_OVERRIDE
            )
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
            except TransportFailure as error:
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
            if isinstance(proposed_response, RetryAttempt):
                if not compiled.contract.is_idempotent:
                    from eazy_sdk.clients.base import UnsafeReplayError

                    raise UnsafeReplayError("middleware replay requires an idempotent operation")
                values = apply_patch_atomic(values, proposed_response.patch)
                attempt_kind = proposed_response.kind
                continue
            if isinstance(proposed_response, RedirectTo):
                if redirect_remaining <= 0:
                    from eazy_sdk.clients.base import RedirectLimitExceeded

                    raise RedirectLimitExceeded("redirect budget exhausted")
                redirect_remaining -= 1
                next_url = urljoin(current_url, proposed_response.url)
                attempt_kind = "redirect"
                continue
            if (
                response.status_code in options.retry.retry_statuses
                and transport_remaining > 0
                and number < hard_attempt_limit
            ):
                if not compiled.contract.is_idempotent:
                    from eazy_sdk.clients.base import UnsafeReplayError

                    raise UnsafeReplayError("retry policy requires an idempotent operation")
                transport_remaining -= 1
                retry_number += 1
                await options.retry.wait(retry_number)
                attempt_kind = "response-retry"
                continue
            signal = inspect_signals(
                cast(
                    Any,
                    (
                        *self.runtime.signals,
                        *(
                            signal
                            for protection in protections
                            for signal in getattr(protection, "signals", ())
                        ),
                    ),
                ),
                cast(ResponseContext[object], response_context),
                scope,
            )
            outcome = (
                compiled.contract.responses.inspect(response_context)
                if isinstance(compiled.contract.responses, Responses)
                else None
            )
            event = signal if isinstance(signal, SignalMatch) else outcome
            if isinstance(event, (SignalMatch, ErrorOutcome)):
                action = await react(
                    cast(Any, event),
                    protection_reactions,
                    solvers=self.runtime.solvers,
                    compiled=cast(Any, compiled),
                    values=attempt_values,
                    solve_context=SolveContext(
                        compiled.plan.operation,
                        cast(ResponseContext[object], response_context),
                        number,
                    ),
                    budget=reaction_budget,
                    body=prepared.body,
                    origin_may_have_executed=True,
                )
                if isinstance(action, ReplayAction):
                    values = apply_patch_atomic(values, action.patch)
                    attempt_kind = "reaction"
                    continue
            if (
                response.status_code == 401
                and auth_remaining > 0
                and number < hard_attempt_limit
                and _has_refreshable_security(auth_executions, self.runtime.auth)
            ):
                if not compiled.contract.is_idempotent:
                    from eazy_sdk.clients.base import UnsafeReplayError

                    raise UnsafeReplayError("auth refresh requires an idempotent operation")
                await _refresh_security(
                    auth_executions,
                    self.runtime.auth,
                    self.resolution_graph,
                )
                auth_remaining -= 1
                attempt_kind = "auth-refresh"
                continue
            location = cast(Any, response.headers).get("location")
            if response.status_code in {301, 302, 303, 307, 308} and location is not None:
                if redirect_remaining <= 0:
                    from eazy_sdk.clients.base import RedirectLimitExceeded

                    raise RedirectLimitExceeded("redirect budget exhausted")
                redirect_remaining -= 1
                next_url = urljoin(response.url or current_url, location)
                effective_method = redirect_method or compiled.contract.method
                if (response.status_code == 303 and effective_method != "HEAD") or (
                    response.status_code in {301, 302} and effective_method == "POST"
                ):
                    redirect_method = "GET"
                    redirect_omit_body = True
                attempt_kind = "redirect"
                continue
            if compiled.contract.raw_response:
                return ExecutionResult(cast(T, response), response)
            assert outcome is not None
            if isinstance(outcome, SuccessOutcome):
                return ExecutionResult(cast(T, outcome.value), response)
            outcome.unwrap()
            raise AssertionError("terminal response outcome unexpectedly returned")
        from eazy_sdk.clients.base import AttemptLimitExceeded

        raise AttemptLimitExceeded("hard attempt budget exhausted")

    async def _acquire_mandatory_protections(
        self,
        compiled: Any,
        options: Any,
        preparation: _MandatoryPreparation,
    ) -> dict[int, object]:
        results: dict[int, object] = {}
        for flow, acquire, verify in preparation.flows:
            acquired = await self.execute(acquire.call({}), options=options)
            current: object = acquired.value
            if flow.solve:
                solver = self.runtime.protection_solvers.get(flow.requirement)
                assert solver is not None
                current = await solver.solve(
                    current,
                    SolveContext(
                        compiled.plan.operation,
                        None,
                        0,
                        network_identity=cast(Any, self.runtime.network_identity),
                    ),
                )
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


def _project_body(
    compiled: Any,
    values: OperationValues,
    models: ModelAdapterRegistry,
    private_values: Mapping[int, object] | None = None,
) -> object:
    projection = compiled.body_projection
    if projection is None:
        return _NO_BODY_DOCUMENT_OVERRIDE
    source = {
        name: copy.deepcopy(values.require(slot))
        for name, slot in compiled.projection_slots.items()
    }
    try:
        projected = projection.using(source)
    except Exception:
        raise BodyProjectionError(
            f"body projection {projection.fingerprint_name!r} failed for "
            f"operation {compiled.contract.operation_id!r}"
        ) from None
    mode: ModelDumpMode = "python" if isinstance(projection.encoding, MultipartBody) else "json"
    try:
        if compiled.private_wire_writers:
            document = models.dump(projected, mode=mode)
            if not isinstance(document, Mapping):
                raise ModelAdapterError("projected target must produce an object document")
            selected = tuple(
                (
                    writer,
                    _select_protection_field(
                        (private_values or {})[id(writer.requirement)],
                        writer.result_field,
                    ),
                )
                for writer in compiled.private_wire_writers
            )
            document = copy.deepcopy(dict(document))
            for writer, private_value in selected:
                _write_private_wire_value(
                    document,
                    writer.validation_path,
                    private_value,
                    wire_path=writer.path,
                )
            projected = document
        validated = models.load(projection.target, projected)
        document = models.dump(validated, mode=mode)
        for path in compiled.body_signature_paths:
            if isinstance(document, Mapping) and _wire_path_present(document, path):
                raise WriterConflictError(
                    "body projection and signature output collide at "
                    f"{'.'.join(path)!r}"
                )
        return document
    except WriterConflictError:
        raise
    except Exception as exc:
        path = _projection_validation_path(exc)
        detail = f" at {path}" if path is not None else ""
        raise BodyProjectionError(
            f"body projection {projection.fingerprint_name!r} target validation failed"
            f"{detail} for operation {compiled.contract.operation_id!r}"
        ) from None


def _write_private_wire_value(
    document: dict[str, object],
    path: tuple[str, ...],
    value: object,
    *,
    wire_path: tuple[str, ...],
) -> None:
    if wire_path != path and _wire_path_present(document, wire_path):
        raise WriterConflictError(
            "body projection and private wire writer collide at "
            f"{'.'.join(wire_path)!r}"
        )
    current = document
    for component in path[:-1]:
        nested = current.get(component)
        if nested is None:
            created: dict[str, object] = {}
            current[component] = created
            current = created
            continue
        if not isinstance(nested, Mapping):
            raise WriterConflictError(
                "private wire writer cannot traverse occupied path "
                f"{'.'.join(wire_path)!r}"
            )
        copied = dict(nested)
        current[component] = copied
        current = copied
    terminal = path[-1]
    if terminal in current:
        raise WriterConflictError(
            "body projection and private wire writer collide at "
            f"{'.'.join(wire_path)!r}"
        )
    current[terminal] = copy.deepcopy(value)


def _wire_path_present(document: Mapping[str, object], path: tuple[str, ...]) -> bool:
    current: object = document
    for component in path:
        if not isinstance(current, Mapping) or component not in current:
            return False
        current = current[component]
    return True


def _projection_validation_path(error: Exception) -> str | None:
    errors = getattr(error, "errors", None)
    if callable(errors):
        try:
            entries = errors()
        except Exception:
            entries = ()
        if entries and isinstance(entries[0], Mapping):
            location = entries[0].get("loc")
            if isinstance(location, tuple | list):
                return ".".join(str(item) for item in location)
    if isinstance(error, ModelAdapterError):
        message = str(error)
        marker = "missing required field "
        if marker in message:
            return message.partition(marker)[2]
    return None


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


def _select_protection_field(result: object, field_name: str) -> object:
    if isinstance(result, Mapping):
        try:
            return result[field_name]
        except KeyError as exc:
            raise TypeError(f"protection result field is missing: {field_name}") from exc
    try:
        return getattr(result, field_name)
    except AttributeError as exc:
        raise TypeError(f"protection result field is missing: {field_name}") from exc


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
        split.scheme, split.netloc, split.path, contract.method, contract.compile().plan.operation
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
            raise CryptoStreamingUnsupported(
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
