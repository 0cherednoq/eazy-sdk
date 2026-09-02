from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, cast

import pytest
from eazy_sdk_presets import cloudflare, host, json_field, operation, recaptcha
from eazy_sdk_presets import header as preset_header
from zapros import AsyncBaseHandler, Request, Response

from eazy_sdk import AsyncApi, AsyncClient, ClientConfig, PlanError, SyncApi, api
from eazy_sdk.clients import CallOptions
from eazy_sdk.clients.async_client import _AsyncClientCore
from eazy_sdk.clients.executor import ExecutionRuntime
from eazy_sdk.clients.sync_client import _SyncClientCore
from eazy_sdk.ext import (
    OperationIdentity,
    ScopeContext,
)
from eazy_sdk.handlers import (
    AutomaticHeaderPolicy,
    CapabilityLevel,
    HandlerProfile,
    RedirectControl,
)
from eazy_sdk.protection.advanced import (
    ChallengeSolverBindings,
    MalformedSignal,
    MissingSolverError,
    SignalMatch,
    _inspect_signals,
    bind_challenge_solver,
)
from eazy_sdk.request import (
    Header,
    JsonField,
)
from eazy_sdk.request.prepared import BufferedBody, HttpProtocol, PreparedRequest
from eazy_sdk.response import (
    Headers,
    Json,
    NormalizedResponse,
    ResponseContext,
    Responses,
    Success,
    callable_parser,
)

FIXTURES = Path(__file__).with_name("fixtures")


class SyncRecaptchaApi(SyncApi):
    @api.post(
        "/create",
        operation_id="create",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def create(
        self,
        *,
        name: Annotated[str, JsonField()],
    ) -> dict[str, object]:
        raise NotImplementedError


class AsyncRecaptchaApi(AsyncApi):
    @api.post(
        "/create",
        operation_id="create",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def create(
        self,
        *,
        name: Annotated[str, JsonField()],
    ) -> dict[str, object]:
        raise NotImplementedError


class SyncClearanceApi(SyncApi):
    @api.get(
        "/protected",
        operation_id="protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def protected(self) -> dict[str, object]:
        raise NotImplementedError


class AsyncClearanceApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(self) -> dict[str, object]:
        raise NotImplementedError


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def capabilities() -> HandlerProfile:
    verified = CapabilityLevel.CAPTURE_VERIFIED
    return HandlerProfile(
        protocols=frozenset({HttpProtocol.HTTP_1_1}),
        exact_target=verified,
        header_order=verified,
        header_casing=verified,
        duplicate_headers=verified,
        preencoded_body=verified,
        manual_cookie_field=verified,
        automatic_headers=AutomaticHeaderPolicy.MATERIALIZED,
        redirects=RedirectControl.FORCED_OFF,
        replayable_streams=verified,
    )


def challenge_context(
    body: bytes = b'<html><div data-sitekey="site">Managed challenge</div></html>',
    *,
    marker: str | None = "challenge",
) -> ResponseContext[object]:
    headers = [("content-type", "text/html")]
    if marker is not None:
        headers.append(("cf-mitigated", marker))
    return ResponseContext(
        NormalizedResponse(403, "https://api.test/", "GET", Headers(headers), body)
    )


def test_cloudflare_uses_definitive_documented_header_not_status() -> None:
    guard = cloudflare.challenge_pages(scope=host("api.test"))
    scope = ScopeContext("https", "api.test", "/", "GET", OperationIdentity("get"))
    matched = _inspect_signals((guard.signal,), challenge_context(), scope)
    assert isinstance(matched, SignalMatch)
    challenge = cast(cloudflare.CloudflareChallenge, matched.value)
    assert challenge.site_key == "site"
    assert challenge.kind is cloudflare.CloudflareChallengeKind.MANAGED
    assert _inspect_signals((guard.signal,), challenge_context(marker=None), scope) is None


def test_cloudflare_unknown_variant_stays_a_match_and_secrets_are_redacted() -> None:
    guard = cloudflare.challenge_pages(scope=host("api.test"))
    scope = ScopeContext("https", "api.test", "/", "GET", OperationIdentity("get"))
    matched = _inspect_signals(
        (guard.signal,), challenge_context(b"<html>new mode</html>"), scope
    )
    assert isinstance(matched, SignalMatch)
    challenge = cast(cloudflare.CloudflareChallenge, matched.value)
    assert challenge.kind is cloudflare.CloudflareChallengeKind.UNKNOWN
    solution = cloudflare.CloudflareClearance((cloudflare.SecretCookie("cf_clearance", "secret"),))
    assert "secret" not in repr(solution)
    assert "secret" not in repr(solution.cookies[0])


def test_binding_is_immutable_and_solver_binding_is_separate_by_identity() -> None:
    class Solver:
        async def solve(self, challenge: Any, context: Any) -> Any:
            return cloudflare.CloudflareClearance(())

    implementation = Solver()
    original = cloudflare.challenge_pages(scope=host("api.test"))
    changed = original.extend_detection(lambda context: True)
    assert changed is not original
    assert changed.customized == frozenset({"detection"})
    bindings = ChallengeSolverBindings(
        bind_challenge_solver(
            original.solver,
            implementation,
        )
    )
    assert bindings.get(original.solver) is implementation


def test_all_recaptcha_lifecycles_have_distinct_factories() -> None:
    scope = host("api.test")
    values = (
        recaptcha.v2_checkbox(scope=scope),
        recaptcha.v2_invisible(scope=scope),
        recaptcha.v3_action(scope=scope, site_key="site", action="login"),
        recaptcha.enterprise_checkbox(scope=scope),
        recaptcha.enterprise_score_action(
            scope=scope, project="project", site_key="site", action="login"
        ),
        recaptcha.enterprise_policy_based_challenge(scope=scope),
        cloudflare.turnstile_widget(scope=scope),
        cloudflare.turnstile_preclearance(
            scope=scope, site_key="site", page_url="https://api.test/challenge"
        ),
    )
    assert len({str(item.id) for item in values}) == len(values)


@pytest.mark.parametrize(
    ("factory", "filename", "mode"),
    [
        (
            cloudflare.turnstile_widget,
            "turnstile_managed.html",
            cloudflare.TurnstileMode.MANAGED,
        ),
        (
            cloudflare.turnstile_widget,
            "turnstile_invisible.html",
            cloudflare.TurnstileMode.INVISIBLE,
        ),
        (recaptcha.v2_checkbox, "recaptcha_checkbox.html", recaptcha.RecaptchaMode.V2_CHECKBOX),
        (
            recaptcha.v2_invisible,
            "recaptcha_invisible.html",
            recaptcha.RecaptchaMode.V2_INVISIBLE,
        ),
        (
            recaptcha.enterprise_checkbox,
            "recaptcha_enterprise.html",
            recaptcha.RecaptchaMode.ENTERPRISE_CHECKBOX,
        ),
    ],
)
def test_widget_fixture_matrix(factory: Any, filename: str, mode: object) -> None:
    preset = factory(scope=host("api.test"))
    scope = ScopeContext("https", "api.test", "/", "GET", OperationIdentity("get"))
    context = challenge_context(fixture(filename), marker=None)
    matched = _inspect_signals((preset.signal,), context, scope)
    assert isinstance(matched, SignalMatch)
    challenge = cast(Any, matched.value)
    assert challenge.mode is mode


def test_widget_negative_and_malformed_fixtures() -> None:
    preset = cloudflare.turnstile_widget(scope=host("api.test"))
    scope = ScopeContext("https", "api.test", "/", "GET", OperationIdentity("get"))
    assert (
        _inspect_signals(
            (preset.signal,),
            challenge_context(fixture("ordinary_page.html"), marker=None),
            scope,
        )
        is None
    )
    malformed = _inspect_signals(
        (preset.signal,),
        challenge_context(fixture("turnstile_malformed.html"), marker=None),
        scope,
    )
    assert isinstance(malformed, MalformedSignal)


def test_widget_unknown_oversized_and_custom_parser_paths() -> None:
    scope = ScopeContext("https", "api.test", "/", "GET", OperationIdentity("get"))
    preset = cloudflare.turnstile_widget(scope=host("api.test"))
    unknown = _inspect_signals(
        (preset.signal,),
        challenge_context(
            b'<div class="cf-turnstile" data-sitekey="site" data-size="future"></div>',
            marker=None,
        ),
        scope,
    )
    assert isinstance(unknown, SignalMatch)
    assert (
        cast(cloudflare.TurnstileChallenge, unknown.value).mode is cloudflare.TurnstileMode.UNKNOWN
    )

    oversized = _inspect_signals(
        (preset.signal,),
        challenge_context(
            b'<div class="cf-turnstile" data-sitekey="site">' + b"x" * 1_000_001,
            marker=None,
        ),
        scope,
    )
    assert isinstance(oversized, MalformedSignal)

    def parse_custom(context: Any) -> Any:
        from eazy_sdk.ext import ParsedValue

        return ParsedValue(
            cloudflare.TurnstileChallenge("custom", cloudflare.TurnstileMode.MANAGED)
        )

    replaced = preset.replace_parser(
        callable_parser(cloudflare.TurnstileChallenge, parse_custom)
    )
    custom = _inspect_signals(
        (replaced.signal,),
        challenge_context(fixture("turnstile_managed.html"), marker=None),
        scope,
    )
    assert isinstance(custom, SignalMatch)
    assert cast(cloudflare.TurnstileChallenge, custom.value).site_key == "custom"


def test_fixture_corpus_is_sanitized_and_complete() -> None:
    expected = {
        "cloudflare_managed.html",
        "cloudflare_unknown.html",
        "ordinary_page.html",
        "recaptcha_checkbox.html",
        "recaptcha_enterprise.html",
        "recaptcha_invisible.html",
        "turnstile_invisible.html",
        "turnstile_malformed.html",
        "turnstile_managed.html",
    }
    assert {item.name for item in FIXTURES.glob("*.html")} == expected
    corpus = b"\n".join((FIXTURES / name).read_bytes() for name in sorted(expected)).lower()
    assert b"cf_clearance=" not in corpus
    assert b"secret-token" not in corpus
    assert b"cookie:" not in corpus


@pytest.mark.parametrize("client_type", [_SyncClientCore, _AsyncClientCore])
@pytest.mark.asyncio
async def test_v3_before_call_solves_and_applies_before_first_send(client_type: Any) -> None:
    class Solver:
        calls = 0

        async def solve(self, challenge: Any, context: Any) -> recaptcha.RecaptchaToken:
            self.calls += 1
            assert challenge.action == "create"
            assert context.response is None
            return recaptcha.RecaptchaToken("secret-token")

    solver = Solver()
    preset = recaptcha.v3_action(
        scope=operation("create"),
        site_key="site-public",
        action="create",
        apply=json_field("captcha_token"),
    )

    async def emit(request: PreparedRequest, *, options: object) -> NormalizedResponse[object]:
        assert isinstance(request.body, BufferedBody)
        assert json.loads(request.body.content) == {"name": "Ada", "captcha_token": "secret-token"}
        return NormalizedResponse(
            200,
            request.url,
            "POST",
            Headers((("content-type", "application/json"),)),
            b'{"ok":true}',
        )

    runtime = ExecutionRuntime(
        capabilities(),
        emit,
        "https://api.test",
        before_call_policies=(preset,),
        challenge_solvers=ChallengeSolverBindings(
            bind_challenge_solver(preset.solver, solver)
        ),
    )
    client = client_type(runtime)
    if client_type is _AsyncClientCore:
        result = await AsyncRecaptchaApi(client).create(name="Ada")
    else:
        result = await asyncio.to_thread(SyncRecaptchaApi(client).create, name="Ada")
    assert result == {"ok": True}
    assert solver.calls == 1


@pytest.mark.asyncio
async def test_preclearance_until_expiry_is_session_scoped_singleflight() -> None:
    class Solver:
        calls = 0

        async def solve(self, challenge: Any, context: Any) -> cloudflare.CloudflareClearance:
            self.calls += 1
            return cloudflare.CloudflareClearance(
                (cloudflare.SecretCookie("cf_clearance", "secret-cookie"),),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )

    solver = Solver()
    preset = cloudflare.turnstile_preclearance(
        scope=operation("protected"),
        site_key="site-public",
        page_url="https://api.test/challenge",
    )

    async def emit(request: PreparedRequest, *, options: object) -> NormalizedResponse[object]:
        assert any(
            field.name.lower() == b"cookie" and b"cf_clearance=secret-cookie" in field.value
            for field in request.headers
        )
        return NormalizedResponse(
            200,
            request.url,
            "GET",
            Headers((("content-type", "application/json"),)),
            b'{"ok":true}',
        )

    runtime = ExecutionRuntime(
        capabilities(),
        emit,
        "https://api.test",
        before_call_policies=(preset,),
        challenge_solvers=ChallengeSolverBindings(
            bind_challenge_solver(preset.solver, solver)
        ),
    )
    client = _AsyncClientCore(runtime)
    api = AsyncClearanceApi(client)
    await asyncio.gather(api.protected(), api.protected())
    assert solver.calls == 1


def test_challenge_page_reaction_rebuilds_cookie_before_replay() -> None:
    class Solver:
        async def solve(self, challenge: Any, context: Any) -> cloudflare.CloudflareClearance:
            return cloudflare.CloudflareClearance(
                (
                    cloudflare.SecretCookie("cf_clearance", "secret-cookie"),
                    cloudflare.SecretCookie("__cf_bm", "managed-cookie"),
                ),
            )

    solver = Solver()
    preset = cloudflare.challenge_pages(scope=host("api.test"))

    class GuardedApi(SyncApi):
        @api.get(
            "/guarded",
            operation_id="guarded",
            responses=Responses(success=(Success(200, Json(dict)),)),
        )
        def guarded(self, *, options: CallOptions | None = None) -> dict[str, object]:
            raise NotImplementedError

    calls = 0

    def emit(request: PreparedRequest, *, options: object) -> NormalizedResponse[object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return NormalizedResponse(
                403,
                request.url,
                "GET",
                Headers((("content-type", "text/html"), ("cf-mitigated", "challenge"))),
                fixture("cloudflare_managed.html"),
            )
        assert any(
            field.name.lower() == b"cookie"
            and b"cf_clearance=secret-cookie" in field.value
            and b"__cf_bm=managed-cookie" in field.value
            for field in request.headers
        )
        return NormalizedResponse(
            200,
            request.url,
            "GET",
            Headers((("content-type", "application/json"),)),
            b'{"ok":true}',
        )

    runtime = ExecutionRuntime(
        capabilities(),
        emit,
        "https://api.test",
        challenge_policies=(preset,),
        challenge_solvers=ChallengeSolverBindings(
            bind_challenge_solver(preset.solver, solver)
        ),
    )
    result = GuardedApi(_SyncClientCore(runtime)).guarded(options=CallOptions(max_attempts=2))
    assert result == {"ok": True}


def test_with_protection_is_immutable_and_rejects_duplicate_policy_identity() -> None:
    original = ClientConfig(auth_retries=0)
    preset = cloudflare.challenge_pages(scope=host("api.test"))

    configured = original.with_protection(preset)

    assert original.challenge_policies == ()
    assert configured.challenge_policies == (preset,)
    assert configured.challenge_solvers.bindings == ()
    with pytest.raises(ValueError, match="duplicate protection policy identity"):
        configured.with_protection(
            cloudflare.challenge_pages(scope=host("other.test"))
        )


@pytest.mark.asyncio
async def test_unbound_installed_preset_fails_before_public_handler() -> None:
    class Handler(AsyncBaseHandler):
        async def ahandle(self, request: Request) -> Response:
            raise AssertionError("handler must not run")

        async def aclose(self) -> None:
            return None

    preset = recaptcha.v3_action(
        scope=operation("create"),
        site_key="site-public",
        action="create",
        apply=json_field("captcha_token"),
    )
    config = ClientConfig(auth_retries=0).with_protection(preset)

    async with AsyncClient(
        base_url="https://api.test",
        handler=Handler(),
        config=config,
    ) as client:
        with pytest.raises(
            MissingSolverError,
            match=r"missing solver: google\.recaptcha-v3",
        ):
            await AsyncRecaptchaApi(client).create(name="Ada")


def test_incompatible_application_is_rejected_before_solver_or_transport() -> None:
    calls = {"solver": 0, "emit": 0}

    class Solver:
        async def solve(self, challenge: Any, context: Any) -> recaptcha.RecaptchaToken:
            calls["solver"] += 1
            return recaptcha.RecaptchaToken("secret")

    preset = recaptcha.v3_action(
        scope=operation("protected"),
        site_key="site",
        action="read",
        apply=preset_header("X-Captcha"),
    )

    class IncompatibleApi(SyncApi):
        @api.get(
            "/protected",
            operation_id="protected",
            responses=Responses(success=(Success(200, Json(dict)),)),
        )
        def protected(
            self,
            *,
            captcha: Annotated[str | None, Header("X-Captcha")] = None,
        ) -> dict[str, object]:
            raise NotImplementedError

    def emit(request: PreparedRequest, *, options: object) -> NormalizedResponse[object]:
        calls["emit"] += 1
        raise AssertionError("transport must not run")

    runtime = ExecutionRuntime(
        capabilities(),
        emit,
        "https://api.test",
        before_call_policies=(preset,),
        challenge_solvers=ChallengeSolverBindings(
            bind_challenge_solver(preset.solver, Solver())
        ),
    )
    with pytest.raises(PlanError, match="private binding conflicts"):
        IncompatibleApi(_SyncClientCore(runtime)).protected()
    assert calls == {"solver": 0, "emit": 0}
