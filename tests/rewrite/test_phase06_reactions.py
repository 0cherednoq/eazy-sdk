from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk._internal import OperationIdentity, RequestScope, ScopeContext
from eazy_sdk.ext import Malformed, NoMatch, ParsedValue
from eazy_sdk.protection.advanced import ResponseSignal, SignalMatch, _inspect_signals
from eazy_sdk.response import NormalizedResponse, ResponseContext, callable_parser
from eazy_sdk.response.cases import ParseAttempt


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
    result = _inspect_signals((signal,), response(b"cf-challenge ray=ray_1"), scope)
    assert isinstance(result, SignalMatch)
    assert result.value == CloudflareChallenge("ray_1")
    assert prefilter_calls == 1

    no_prefilter = _inspect_signals(
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
