from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from eazy_sdk._internal import (
    CompiledContract,
    GraphError,
    InputField,
    OperationIdentity,
    OperationValues,
    RequestLocation,
    RequestScope,
    ScopeContext,
    apply_patch_atomic,
    compile_endpoint,
)
from eazy_sdk.ext import (
    BufferedBody,
    ErrorOutcome,
    Malformed,
    NoMatch,
    ParsedValue,
    RequestPreparer,
    callable_parser,
)
from eazy_sdk.protection import (
    BeforeCall,
    ReactionBudget,
    ReplayDeniedError,
    ResponseReaction,
    ResponseSignal,
    SignalMatch,
    SolutionFreshness,
    SolveContext,
    SolverRegistry,
    SolverRequirement,
    body_field,
    header_target,
    idempotency_key,
    inspect_signals,
    react,
    safe_method,
    validate_before_call_cycles,
)
from eazy_sdk.request import Header, JsonBody, Path
from eazy_sdk.response import (
    Error,
    Json,
    NormalizedResponse,
    ResponseContext,
    Responses,
    Success,
)
from eazy_sdk.response.cases import ParseAttempt


@dataclass(frozen=True)
class Contract:
    operation_id: str = "createPayment"
    method: str = "POST"
    path: str = "/payments/{payment_id}"
    input_fields: tuple[InputField, ...] = (
        InputField("payment_id", "payment_id", str, True, RequestLocation.PATH, Path("payment_id")),
        InputField(
            "idempotency_key",
            "Idempotency-Key",
            str,
            False,
            RequestLocation.HEADER,
            Header("Idempotency-Key"),
        ),
        InputField(
            "clearance", "X-Clearance", str, False, RequestLocation.HEADER, Header("X-Clearance")
        ),
        InputField("body", "body", dict[str, object], True, RequestLocation.BODY, JsonBody()),
    )
    responses: object = "responses"


def compiled_values(
    *, idempotency: str | None = "idem_1"
) -> tuple[CompiledContract[object], OperationValues, BufferedBody]:
    compiled: CompiledContract[object] = compile_endpoint(Contract())
    request: dict[str, object] = {
        "payment_id": "p1",
        "body": {"amount": 10, "captcha": None},
    }
    if idempotency is not None:
        request["idempotency_key"] = idempotency
    values = OperationValues.from_bound(
        compiled.plan.shape,
        compiled.bind_input(request),
    )
    prepared = RequestPreparer("https://api.example").prepare(compiled, values)
    assert isinstance(prepared.body, BufferedBody)
    return compiled, values, prepared.body


@dataclass(frozen=True)
class CloudflareChallenge:
    ray_id: str


def challenge_parser(response: ResponseContext[object]) -> ParseAttempt[CloudflareChallenge]:
    if b"cf-challenge" not in response.bytes:
        return NoMatch()
    if b"ray=" not in response.bytes:
        return Malformed(ValueError("ray id missing"))
    return ParsedValue(CloudflareChallenge("ray_1"))


def response(body: bytes, *, status: int = 503) -> ResponseContext[object]:
    return ResponseContext(
        NormalizedResponse(
            status,
            "https://api.example/payments",
            "POST",
            {"Content-Type": "text/html", "cf-mitigated": "challenge"},
            body,
        )
    )


def test_external_signal_prefilter_and_parser_run_before_endpoint_json_cases() -> None:
    prefilter_calls = 0

    def prefilter(context: ResponseContext[object]) -> bool:
        nonlocal prefilter_calls
        prefilter_calls += 1
        return context.headers.get("cf-mitigated") == "challenge"

    signal = ResponseSignal(
        "cloudflare.challenge",
        RequestScope(hosts=frozenset({"api.example"})),
        CloudflareChallenge,
        callable_parser(CloudflareChallenge, challenge_parser),
        prefilter=prefilter,
    )
    scope = ScopeContext(
        "https", "api.example", "/payments", "POST", OperationIdentity("createPayment")
    )
    result = inspect_signals((signal,), response(b"cf-challenge ray=ray_1"), scope)
    assert isinstance(result, SignalMatch)
    assert result.value == CloudflareChallenge("ray_1")
    assert prefilter_calls == 1

    no_prefilter = inspect_signals(
        (signal,),
        ResponseContext(
            NormalizedResponse(
                200,
                "https://api.example/payments",
                "POST",
                {"Content-Type": "application/json"},
                b'{"id":"p1"}',
            )
        ),
        scope,
    )
    assert no_prefilter is None
    assert prefilter_calls == 2


@dataclass(frozen=True)
class CaptchaChallenge:
    captcha_id: str


@dataclass(frozen=True)
class CaptchaSolution:
    answer: str


class CaptchaSolver:
    def __init__(self) -> None:
        self.calls = 0

    async def solve(self, challenge: CaptchaChallenge, context: SolveContext) -> CaptchaSolution:
        self.calls += 1
        assert challenge.captcha_id == "cap_1"
        assert context.response is not None
        return CaptchaSolution("42")


@pytest.mark.asyncio
async def test_documented_captcha_reaction_solves_typed_event_and_applies_atomic_patch() -> None:
    captcha_case = Error(409, Json(CaptchaChallenge))
    responses: Responses[object] = Responses(
        success=(Success(201, Json(dict)),), errors=(captcha_case,)
    )
    response_context = ResponseContext(
        NormalizedResponse(
            409,
            "https://api.example/payments",
            "POST",
            {"Content-Type": "application/json"},
            b'{"captcha_id":"cap_1"}',
        )
    )
    outcome = responses.inspect(response_context)
    assert isinstance(outcome, ErrorOutcome)
    requirement: SolverRequirement[CaptchaChallenge, CaptchaSolution] = SolverRequirement("captcha")
    solver = CaptchaSolver()
    registry = SolverRegistry()
    registry.register(requirement, solver)
    reaction: ResponseReaction[CaptchaChallenge, CaptchaSolution] = ResponseReaction(
        captcha_case,
        requirement,
        body_field("captcha", value_field="answer"),
        idempotency_key("Idempotency-Key"),
    )
    compiled, values, prepared_body = compiled_values()
    action = await react(
        outcome,
        (cast(ResponseReaction[object, object], reaction),),
        solvers=registry,
        compiled=compiled,
        values=values,
        solve_context=SolveContext(OperationIdentity("createPayment"), response_context, attempt=1),
        budget=ReactionBudget(1),
        body=prepared_body,
        origin_may_have_executed=True,
    )
    assert action is not None
    changed = apply_patch_atomic(values, action.patch)
    assert compiled.body_slot is not None
    changed_body = changed.require(compiled.body_slot)
    assert isinstance(changed_body, dict)
    assert changed_body["captcha"] == "42"
    assert solver.calls == 1


@pytest.mark.asyncio
async def test_missing_solver_leaves_documented_outcome_and_unsafe_post_is_denied() -> None:
    captcha_case = Error(409, Json(CaptchaChallenge))
    outcome = ErrorOutcome(CaptchaChallenge("cap_1"), captcha_case, response(b"cf-challenge ray=x"))
    requirement: SolverRequirement[CaptchaChallenge, CaptchaSolution] = SolverRequirement("captcha")
    reaction: ResponseReaction[CaptchaChallenge, CaptchaSolution] = ResponseReaction(
        captcha_case,
        requirement,
        header_target("X-Clearance", value_field="answer"),
        safe_method(),
    )
    compiled, values, body = compiled_values(idempotency=None)
    no_solver = await react(
        cast(ErrorOutcome[object], outcome),
        (cast(ResponseReaction[object, object], reaction),),
        solvers=SolverRegistry(),
        compiled=compiled,
        values=values,
        solve_context=SolveContext(OperationIdentity("createPayment"), outcome.context, 1),
        budget=ReactionBudget(1),
        body=body,
        origin_may_have_executed=True,
    )
    assert no_solver is None

    registry = SolverRegistry()
    registry.register(requirement, CaptchaSolver())
    with pytest.raises(ReplayDeniedError, match="unsafe replay"):
        await react(
            cast(ErrorOutcome[object], outcome),
            (cast(ResponseReaction[object, object], reaction),),
            solvers=registry,
            compiled=compiled,
            values=values,
            solve_context=SolveContext(OperationIdentity("createPayment"), outcome.context, 1),
            budget=ReactionBudget(1),
            body=body,
            origin_may_have_executed=True,
        )


class FlowContract:
    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        self.before: tuple[BeforeCall, ...] = ()


def test_before_call_cycles_are_rejected_before_io() -> None:
    acquire = FlowContract("acquire")
    protected = FlowContract("protected")
    protected.before = (
        BeforeCall(
            acquire,
            header_target("X-Challenge"),
            freshness=SolutionFreshness.PER_CALL,
        ),
    )
    validate_before_call_cycles((protected, acquire))
    acquire.before = (
        BeforeCall(
            protected,
            header_target("X-Challenge"),
            freshness=SolutionFreshness.PER_CALL,
        ),
    )
    with pytest.raises(
        GraphError,
        match=r"protected -> acquire -> protected|acquire -> protected -> acquire",
    ):
        validate_before_call_cycles((protected, acquire))
