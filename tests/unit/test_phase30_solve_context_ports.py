"""Phase 30: ``SolveContext`` transport ports (``fetch``/``identity``) and identity affinity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from eazy_sdk_presets import cloudflare, host
from zapros import AsyncBaseHandler, BaseHandler, Request, Response

from eazy_sdk import AsyncApi, AsyncClient, Client, ClientConfig, SyncApi, api
from eazy_sdk.core.kernel import OperationIdentity
from eazy_sdk.handlers import CONSERVATIVE_HANDLER_PROFILE, HandlerProfile
from eazy_sdk.protection import (
    ChallengeSolveError,
    InstallableProtection,
    ProtectedFetch,
    ProtectionConfigurationError,
    SolveContext,
    TransportIdentity,
    challenge_guard,
    safe_method,
    solution_fields,
)
from eazy_sdk.redaction import REDACTED, redact_url_credentials
from eazy_sdk.request import Header
from eazy_sdk.response import Json, NormalizedResponse, ResponseContext, Responses, Success

BASE_URL = "https://phase30.test"


@dataclass(frozen=True, slots=True)
class Challenge:
    revision: int


@dataclass(frozen=True, slots=True)
class Clearance:
    token: str

    def __repr__(self) -> str:
        return "Clearance(<redacted>)"


class ProtectedApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="phase30.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(
        self,
        *,
        user_agent: Annotated[str, Header("User-Agent")] = "ua-a",
    ) -> dict[str, object]:
        raise NotImplementedError


class SyncProtectedApi(SyncApi):
    @api.get(
        "/protected",
        operation_id="phase30.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    def protected(
        self,
        *,
        user_agent: Annotated[str, Header("User-Agent")] = "ua-a",
    ) -> dict[str, object]:
        raise NotImplementedError


def detect(context: ResponseContext[object]) -> Challenge | None:
    if context.response.status_code != 403:
        return None
    parsed = context.json
    if isinstance(parsed.value, dict) and isinstance(parsed.value.get("revision"), int):
        return Challenge(parsed.value["revision"])
    return None


def _respond(request: Request) -> Response:
    """Origin: ``/asset`` is public; everything else needs a ``clearance`` cookie."""

    path = request.url.pathname
    if path == "/asset":
        return Response(
            200,
            [("Content-Type", "application/octet-stream")],
            content=b"wasm-bytes",
            request=request,
        )
    if path == "/validate":
        body = request.body if isinstance(request.body, bytes) else b""
        return Response(
            200,
            [("Content-Type", "application/json")],
            content=b'{"validated":' + body + b"}",
            request=request,
        )
    cookie = request.headers.get("Cookie") or ""
    if "clearance=" not in cookie:
        return Response(
            403,
            [("Content-Type", "application/json"), ("cf-mitigated", "challenge")],
            content=b'{"revision":7}',
            request=request,
        )
    return Response(
        200,
        [("Content-Type", "application/json")],
        content=b'{"ok":true}',
        request=request,
    )


class OriginHandler(AsyncBaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    async def ahandle(self, request: Request) -> Response:
        self.requests.append(request)
        return _respond(request)

    async def aclose(self) -> None:
        return None


class CloudflareOriginHandler(OriginHandler):
    """Origin whose challenge is a Cloudflare managed-challenge page."""

    async def ahandle(self, request: Request) -> Response:
        self.requests.append(request)
        if "clearance=" in (request.headers.get("Cookie") or ""):
            return _respond(request)
        return Response(
            403,
            [("Content-Type", "text/html"), ("cf-mitigated", "challenge")],
            content=b"<html>managed challenge</html>",
            request=request,
        )


class SyncOriginHandler(BaseHandler):
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def handle(self, request: Request) -> Response:
        self.requests.append(request)
        return _respond(request)

    def close(self) -> None:
        return None


class FetchingSolver:
    """Solver that needs the session: downloads an asset and POSTs a validation."""

    def __init__(self) -> None:
        self.calls = 0
        self.contexts: list[SolveContext] = []
        self.fetched: list[NormalizedResponse[object]] = []

    async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
        self.calls += 1
        self.contexts.append(context)
        asset = await context.fetch(f"{BASE_URL}/asset")
        self.fetched.append(asset)
        validated = await context.fetch(
            f"{BASE_URL}/validate",
            method="POST",
            headers={"Content-Type": "application/json", "X-Solver": "phase30"},
            body=b'{"revision":%d}' % challenge.revision,
        )
        self.fetched.append(validated)
        return Clearance(f"token-{challenge.revision}-{asset.body.decode()}")


def _guard(solver: FetchingSolver) -> InstallableProtection:
    return challenge_guard(
        name="phase30.guard",
        scope=host("phase30.test"),
        detect=detect,
        solver=solver,
        apply=solution_fields(cookies={"clearance": "token"}),
        replay=safe_method(max_replays=1),
    )


@pytest.mark.asyncio
async def test_solver_fetch_uses_the_same_handler_and_bypasses_guards() -> None:
    solver = FetchingSolver()
    handler = OriginHandler()
    config = ClientConfig(timeout=30.0).with_protection(_guard(solver))
    async with AsyncClient(base_url=BASE_URL, handler=handler, config=config) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}

    assert solver.calls == 1
    paths = [request.url.pathname for request in handler.requests]
    assert paths == ["/protected", "/asset", "/validate", "/protected"]
    assert handler.requests[3].headers.get("Cookie") == "clearance=token-7-wasm-bytes"

    asset, validated = solver.fetched
    assert asset.status_code == 200 and asset.body == b"wasm-bytes"
    assert validated.status_code == 200 and validated.body == b'{"validated":{"revision":7}}'

    fetched_asset = handler.requests[1]
    assert fetched_asset.method == "GET"
    assert fetched_asset.headers.get("User-Agent") == "ua-a"
    assert fetched_asset.headers.get("Host") == "phase30.test"
    fetched_validate = handler.requests[2]
    assert fetched_validate.method == "POST"
    assert fetched_validate.headers.get("X-Solver") == "phase30"
    assert fetched_validate.headers.get("Content-Type") == "application/json"
    assert fetched_validate.headers.get("Content-Length") == "14"

    (context,) = solver.contexts
    assert context.identity == TransportIdentity(user_agent="ua-a")
    assert context.request_headers["User-Agent"] == "ua-a"
    assert context.request_headers["Host"] == "phase30.test"
    assert context.deadline is not None
    remaining = context.deadline - datetime.now(UTC)
    assert timedelta(seconds=20) < remaining <= timedelta(seconds=30)


@pytest.mark.asyncio
async def test_solver_fetch_of_the_challenged_url_returns_the_challenge_without_recursion() -> None:
    class ProbingSolver:
        def __init__(self) -> None:
            self.calls = 0
            self.probe: NormalizedResponse[object] | None = None

        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            self.calls += 1
            self.probe = await context.fetch(f"{BASE_URL}/protected")
            return Clearance("probe")

    solver = ProbingSolver()
    handler = OriginHandler()
    config = ClientConfig().with_protection(
        challenge_guard(
            name="phase30.probe",
            scope=host("phase30.test"),
            detect=detect,
            solver=solver,
            apply=solution_fields(cookies={"clearance": "token"}),
        )
    )
    async with AsyncClient(base_url=BASE_URL, handler=handler, config=config) as client:
        assert await ProtectedApi(client).protected() == {"ok": True}

    assert solver.calls == 1
    assert solver.probe is not None and solver.probe.status_code == 403
    assert len(handler.requests) == 3


@pytest.mark.asyncio
async def test_deadline_is_absent_without_a_call_timeout() -> None:
    solver = FetchingSolver()
    config = ClientConfig().with_protection(_guard(solver))
    async with AsyncClient(base_url=BASE_URL, handler=OriginHandler(), config=config) as client:
        await ProtectedApi(client).protected()
    assert solver.contexts[0].deadline is None


@pytest.mark.asyncio
async def test_sync_client_solver_receives_the_same_ports() -> None:
    solver = FetchingSolver()
    handler = SyncOriginHandler()
    config = ClientConfig(timeout=5.0).with_protection(_guard(solver))

    def run() -> dict[str, object]:
        with Client(base_url=BASE_URL, handler=handler, config=config) as client:
            return SyncProtectedApi(client).protected(user_agent="ua-sync")

    assert await asyncio.to_thread(run) == {"ok": True}
    assert [request.url.pathname for request in handler.requests] == [
        "/protected",
        "/asset",
        "/validate",
        "/protected",
    ]
    assert handler.requests[1].headers.get("User-Agent") == "ua-sync"
    assert solver.contexts[0].identity.user_agent == "ua-sync"
    assert solver.contexts[0].deadline is not None


@pytest.mark.asyncio
async def test_identity_change_between_solve_and_replay_invalidates_session_clearance() -> None:
    class CloudflareSolver:
        def __init__(self) -> None:
            self.identities: list[TransportIdentity] = []

        async def solve(
            self,
            challenge: cloudflare.CloudflareChallenge,
            context: SolveContext,
        ) -> cloudflare.CloudflareClearance:
            self.identities.append(context.identity)
            return cloudflare.CloudflareClearance(
                (cloudflare.SecretCookie("clearance", f"cf-{len(self.identities)}"),),
            )

    solver = CloudflareSolver()
    handler = CloudflareOriginHandler()
    config = ClientConfig().with_protection(
        cloudflare.challenge_pages(scope=host("phase30.test"), solver=solver)
    )
    async with AsyncClient(base_url=BASE_URL, handler=handler, config=config) as client:
        service = ProtectedApi(client)
        assert await service.protected(user_agent="ua-a") == {"ok": True}
        assert len(solver.identities) == 1
        assert len(handler.requests) == 2
        first_state = dict(client._runtime._protection_state)
        assert len(first_state) == 1
        (state_a,) = first_state.values()
        assert state_a.identity == TransportIdentity(user_agent="ua-a").fingerprint()

        # Same session, different identity: the clearance acquired as "ua-a" must not be
        # replayed as "ua-b"; the stale state is dropped and the challenge is solved again.
        assert await service.protected(user_agent="ua-b") == {"ok": True}
        assert len(solver.identities) == 2
        assert solver.identities[1].user_agent == "ua-b"
        assert len(handler.requests) == 4
        assert handler.requests[2].headers.get("Cookie") is None
        assert handler.requests[3].headers.get("Cookie") == "clearance=cf-2"
        second_state = dict(client._runtime._protection_state)
        assert len(second_state) == 1
        (state_b,) = second_state.values()
        assert state_b is not state_a
        assert state_b.identity == TransportIdentity(user_agent="ua-b").fingerprint()

        # Unchanged identity keeps reusing the managed clearance.
        assert await service.protected(user_agent="ua-b") == {"ok": True}
        assert len(solver.identities) == 2
        assert len(handler.requests) == 5
        assert handler.requests[4].headers.get("Cookie") == "clearance=cf-2"


@pytest.mark.asyncio
async def test_handler_profile_impersonation_reaches_the_solver_identity() -> None:
    class ImpersonatingHandler(OriginHandler):
        profile = replace(CONSERVATIVE_HANDLER_PROFILE, impersonation="chrome124")

    solver = FetchingSolver()
    config = ClientConfig().with_protection(_guard(solver))
    async with AsyncClient(
        base_url=BASE_URL, handler=ImpersonatingHandler(), config=config
    ) as client:
        await ProtectedApi(client).protected()
    identity = solver.contexts[0].identity
    assert identity.impersonation == "chrome124"
    assert identity.fingerprint() != TransportIdentity(user_agent="ua-a").fingerprint()
    assert HandlerProfile(protocols=frozenset()).impersonation is None


def test_curl_cffi_handlers_declare_impersonation_in_their_profile() -> None:
    curl_cffi = pytest.importorskip("eazy_sdk.handlers.curl_cffi")
    plain = curl_cffi.CurlCffiZaprosHandler()
    impersonating = curl_cffi.AsyncCurlCffiZaprosHandler(impersonate="chrome")
    try:
        assert plain.profile.impersonation is None
        assert impersonating.profile.impersonation == "chrome"
        assert curl_cffi.CURL_CFFI_HANDLER_PROFILE.impersonation is None
    finally:
        plain.close()
        asyncio.run(impersonating.aclose())


def test_transport_identity_redacts_proxy_credentials_and_fingerprints_stably() -> None:
    identity = TransportIdentity(
        user_agent="ua",
        proxy="http://user:s3cret@proxy.test:8080",
        impersonation="chrome",
    )
    rendered = repr(identity)
    assert "s3cret" not in rendered and "user:" not in rendered
    assert f"{REDACTED}@proxy.test:8080" in rendered
    assert identity.proxy == "http://user:s3cret@proxy.test:8080"

    assert identity.fingerprint() == identity.fingerprint()
    assert len(identity.fingerprint()) == 64
    assert identity.fingerprint() != replace(identity, user_agent="other").fingerprint()
    assert identity.fingerprint() != replace(identity, proxy=None).fingerprint()
    assert TransportIdentity().fingerprint() != TransportIdentity(user_agent="").fingerprint()
    assert "s3cret" not in identity.fingerprint()

    assert redact_url_credentials(None) is None
    assert redact_url_credentials("http://proxy.test:8080") == "http://proxy.test:8080"
    assert redact_url_credentials("socks5://a:b@h/p?q") == f"socks5://{REDACTED}@h/p?q"
    assert redact_url_credentials("a:b@h:1") == f"{REDACTED}@h:1"


@pytest.mark.asyncio
async def test_default_solve_context_has_no_transport() -> None:
    context = SolveContext(OperationIdentity("phase30.offline"), None, 1)
    assert context.deadline is None
    assert context.identity == TransportIdentity()
    assert dict(context.request_headers) == {}
    with pytest.raises(ProtectionConfigurationError):
        await context.fetch(f"{BASE_URL}/asset")


@pytest.mark.asyncio
async def test_solver_fetch_rejects_relative_urls_and_wraps_into_solve_error() -> None:
    class RelativeFetchSolver:
        async def solve(self, challenge: Challenge, context: SolveContext) -> Clearance:
            fetch: ProtectedFetch = context.fetch
            await fetch("/asset")
            return Clearance("unreachable")

    handler = OriginHandler()
    config = ClientConfig().with_protection(
        challenge_guard(
            name="phase30.relative",
            scope=host("phase30.test"),
            detect=detect,
            solver=RelativeFetchSolver(),
            apply=solution_fields(cookies={"clearance": "token"}),
        )
    )
    async with AsyncClient(base_url=BASE_URL, handler=handler, config=config) as client:
        with pytest.raises(ChallengeSolveError) as error:
            await ProtectedApi(client).protected()
    assert isinstance(error.value.__cause__, ValueError)
    assert len(handler.requests) == 1
