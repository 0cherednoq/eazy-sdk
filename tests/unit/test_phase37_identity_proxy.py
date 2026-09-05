"""Phase 37: transport identity — declared proxy, one fingerprint per attempt."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Annotated, Any, ClassVar, cast

import pytest
from zapros import AsyncBaseHandler, Request, Response

from eazy_sdk import AsyncApi, AsyncClient, Client, ClientConfig, api
from eazy_sdk.handlers import CONSERVATIVE_HANDLER_PROFILE, EmitOptions, HandlerProfile
from eazy_sdk.protection import Guard, GuardSolution, SolveContext, host
from eazy_sdk.redaction import REDACTED
from eazy_sdk.request import Header
from eazy_sdk.response import Json, ResponseContext, Responses, Success

BASE_URL = "https://phase37.test"
PROXY_A = "http://user:s3cret@proxy-a.test:8080"
PROXY_B = "http://proxy-b.test:8080"


@dataclass(frozen=True, slots=True)
class Challenge:
    revision: int


class ProtectedApi(AsyncApi):
    """No public ``User-Agent``: the guard owns that header."""

    @api.get(
        "/protected",
        operation_id="phase37.protected",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(self) -> dict[str, object]:
        raise NotImplementedError


class PublicUaApi(AsyncApi):
    @api.get(
        "/protected",
        operation_id="phase37.public-ua",
        responses=Responses(success=(Success(200, Json(dict)),)),
    )
    async def protected(
        self,
        *,
        user_agent: Annotated[str, Header("User-Agent")] = "public-ua",
    ) -> dict[str, object]:
        raise NotImplementedError


class Origin(AsyncBaseHandler):
    """Accepts a request only when it carries a token the origin has not revoked."""

    def __init__(self, proxy: str | None = None) -> None:
        self.requests: list[Request] = []
        self.revoked: set[str] = set()
        self.profile = (
            CONSERVATIVE_HANDLER_PROFILE
            if proxy is None
            else replace(CONSERVATIVE_HANDLER_PROFILE, proxy=proxy)
        )

    async def ahandle(self, request: Request) -> Response:
        self.requests.append(request)
        token = request.headers.get("X-Token")
        if token is None or token in self.revoked:
            return Response(
                403,
                [("Content-Type", "application/json")],
                content=b'{"revision":1}',
                request=request,
            )
        return Response(
            200,
            [("Content-Type", "application/json")],
            content=b'{"ok":true}',
            request=request,
        )

    async def aclose(self) -> None:
        return None


def detect(context: ResponseContext[object]) -> Challenge | None:
    if context.response.status_code != 403:
        return None
    document = context.json.value
    if isinstance(document, dict) and isinstance(document.get("revision"), int):
        return Challenge(document["revision"])
    return None


class TokenGuard(Guard[Challenge]):
    scope = host("phase37.test")
    headers: ClassVar[tuple[str, ...]] = ("X-Token",)

    def __init__(self) -> None:
        self.contexts: list[SolveContext] = []

    def detect(self, response: ResponseContext[object]) -> Challenge | None:
        return detect(response)

    async def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
        self.contexts.append(context)
        return self.solution(headers={"X-Token": f"t{len(self.contexts)}"})


class UserAgentGuard(TokenGuard):
    """A guard that rewrites ``User-Agent`` together with its token (the A2 scenario)."""

    headers: ClassVar[tuple[str, ...]] = ("User-Agent", "X-Token")

    async def solve(self, challenge: Challenge, context: SolveContext) -> GuardSolution:
        self.contexts.append(context)
        return self.solution(
            headers={"User-Agent": "solved-ua", "X-Token": f"t{len(self.contexts)}"}
        )


def _tokens(origin: Origin) -> list[str | None]:
    return [request.headers.get("X-Token") for request in origin.requests]


def test_emit_options_carry_only_the_timeout_and_the_profile_declares_the_proxy() -> None:
    assert [field for field in EmitOptions.__dataclass_fields__] == ["timeout"]
    assert not hasattr(EmitOptions(), "proxy")
    profile = replace(CONSERVATIVE_HANDLER_PROFILE, proxy=PROXY_A, impersonation="chrome")
    assert profile.proxy == PROXY_A
    rendered = repr(profile)
    assert "s3cret" not in rendered and f"{REDACTED}@proxy-a.test:8080" in rendered
    assert "impersonation='chrome'" in rendered
    assert repr(CONSERVATIVE_HANDLER_PROFILE).startswith("HandlerProfile(protocols=")


@pytest.mark.asyncio
async def test_declared_proxy_reaches_the_solver_and_binds_the_clearance() -> None:
    origin = Origin(proxy=PROXY_A)
    guard = UserAgentGuard()
    config = ClientConfig(guards=[guard])
    async with AsyncClient(base_url=BASE_URL, handler=origin, config=config) as client:
        service = ProtectedApi(client)
        await service.protected()
        assert guard.contexts[-1].identity.proxy == PROXY_A
        assert "s3cret" not in repr(guard.contexts[-1].identity)
        await service.protected()
        assert len(guard.contexts) == 1

        # The declared proxy changes underneath the runtime: the clearance acquired through
        # proxy A must not be replayed through proxy B.
        client._runtime.handler_profile = replace(origin.profile, proxy=PROXY_B)
        await service.protected()
        assert len(guard.contexts) == 2
        assert guard.contexts[-1].identity.proxy == PROXY_B
        assert _tokens(origin) == [None, "t1", "t1", None, "t2"]


@pytest.mark.asyncio
async def test_guard_that_rewrites_user_agent_keeps_one_identity_per_attempt() -> None:
    origin = Origin()
    guard = UserAgentGuard()
    config = ClientConfig(guards=[guard])
    async with AsyncClient(base_url=BASE_URL, handler=origin, config=config) as client:
        service = ProtectedApi(client)
        for _ in range(3):
            await service.protected()
        # solved once; the stored solution is reused although it rewrites User-Agent
        assert len(guard.contexts) == 1
        assert _tokens(origin) == [None, "t1", "t1", "t1"]

        origin.revoked.add("t1")
        await service.protected()
        assert len(guard.contexts) == 2
        second = guard.contexts[-1]
        # the challenged request carried the guard's User-Agent...
        assert second.request_headers["User-Agent"] == "solved-ua"
        # ...but the identity is computed from the public values of the attempt
        assert second.identity.user_agent is None
        assert [request.headers.get("User-Agent") for request in origin.requests][-2:] == [
            "solved-ua",
            "solved-ua",
        ]
        assert second.identity.fingerprint() == guard.contexts[0].identity.fingerprint()

        # and therefore the new solution is applied on the next call without re-solving
        await service.protected()
        assert len(guard.contexts) == 2
        assert _tokens(origin)[-3:] == ["t1", "t2", "t2"]


@pytest.mark.asyncio
async def test_public_user_agent_change_still_invalidates_the_clearance() -> None:
    origin = Origin()
    guard = TokenGuard()
    config = ClientConfig(guards=[guard])
    async with AsyncClient(base_url=BASE_URL, handler=origin, config=config) as client:
        service = PublicUaApi(client)
        await service.protected()
        await service.protected(user_agent="other-ua")
        assert [context.identity.user_agent for context in guard.contexts] == [
            "public-ua",
            "other-ua",
        ]


def test_httpx_factory_declares_the_proxy_it_configures() -> None:
    httpx = pytest.importorskip("httpx")
    with Client.httpx(base_url=BASE_URL, proxy=PROXY_A) as client:
        assert client.profile.proxy == PROXY_A
        assert "s3cret" not in repr(client.profile)
    with Client.httpx(base_url=BASE_URL, client=httpx.Client()) as borrowed:
        assert borrowed.profile.proxy is None
    with Client.httpx(base_url=BASE_URL, client=httpx.Client(), proxy=PROXY_B) as declared:
        assert declared.profile.proxy == PROXY_B


def test_requests_factory_configures_and_declares_the_proxy() -> None:
    pytest.importorskip("requests")
    with Client.requests(base_url=BASE_URL, proxy=PROXY_B) as client:
        assert client.profile.proxy == PROXY_B
        session = cast(Any, client.handler).session
        assert session.proxies == {"http": PROXY_B, "https": PROXY_B}
    with Client.requests(base_url=BASE_URL) as plain:
        assert plain.profile.proxy is None
        assert cast(Any, plain.handler).session.proxies == {}


def test_curl_cffi_factory_declares_proxy_and_impersonation() -> None:
    pytest.importorskip("curl_cffi")
    with Client.curl_cffi(base_url=BASE_URL, impersonate="chrome", proxy=PROXY_B) as client:
        assert client.profile.impersonation == "chrome"
        assert client.profile.proxy == PROXY_B
    with Client.curl_cffi(base_url=BASE_URL) as plain:
        assert plain.profile.impersonation is None and plain.profile.proxy is None
    assert isinstance(plain.profile, HandlerProfile)
