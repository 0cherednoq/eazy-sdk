"""Phase 59.3: a declared error that means a stale session refreshes it, as a ``401`` does.

The scenario is a mail API that never answers ``401``: a token it no longer accepts comes back
as ``200`` with ``{"status": 403}`` in the envelope. The error class says ``reauth = True``, and
the caller sees the result of the replayed request instead of the error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
import pytest
from pydantic import BaseModel

from eazy_sdk import (
    AsyncApi,
    AsyncRoot,
    ClientConfig,
    Http,
    HttpOperation,
    Identity,
    Resilience,
    api_group,
    op,
)
from eazy_sdk.auth import AuthContext, Placed, session_scheme
from eazy_sdk.clients.base import UnsafeReplayError
from eazy_sdk.core.errors import PlanError
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.models import default_model_adapters
from eazy_sdk.response import ApiError, Const, Error, Json, OneOf
from eazy_sdk.response._mapping import error_case

BASE = "https://mail.example"


@dataclass(slots=True)
class MailSite:
    """Hands out numbered tokens and accepts only the newest one."""

    issued: int = 0
    always_rejects: bool = False
    seen: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.seen.append(f"{request.method} {path} {request.url.params.get('token', '')}".strip())
        if path == "/token":
            self.issued += 1
            return httpx.Response(200, json={"token": f"t{self.issued}"})
        fresh = request.url.params.get("token") == f"t{self.issued}"
        if self.always_rejects or not fresh:
            return httpx.Response(200, json={"status": 403, "body": "token"})
        return httpx.Response(200, json={"status": 200, "body": {"folders": ["Inbox"]}})

    def expire(self) -> None:
        self.issued += 1


class Token(BaseModel):
    token: str


class Folders(BaseModel):
    folders: list[str]


class Envelope(BaseModel):
    status: Annotated[int, Const(200)]
    body: Folders


class TokenRefused(BaseModel):
    status: Annotated[int, OneOf(401, 403)]
    body: Any = None


class TokenRejected(ApiError[TokenRefused]):
    reauth = True


class PlainRejected(ApiError[TokenRefused]):
    pass


class MailSession(BaseModel):
    token: Annotated[str, Placed.query("token")]


MAIL_SESSION = session_scheme(MailSession, name="mail-session")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadToken(HttpOperation[Token]):
    __http__ = Http.get("/token")


@dataclass(frozen=True, slots=True, kw_only=True)
class ListFolders(HttpOperation[Envelope]):
    __http__ = Http.get("/api/folders")


@dataclass(frozen=True, slots=True, kw_only=True)
class ListFoldersPlain(HttpOperation[Envelope]):
    __http__ = Http.get("/api/folders", errors={200: PlainRejected}, inherit_errors=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class MoveFolder(HttpOperation[Envelope]):
    __http__ = Http.post("/api/folders/move")


class LoginApi(AsyncApi):
    token = op(ReadToken)


class MailApi(AsyncApi):
    security = MAIL_SESSION
    errors = ({200: TokenRejected},)

    folders = op(ListFolders)
    folders_plain = op(ListFoldersPlain)
    move = op(MoveFolder)


class MailService:
    def __init__(self) -> None:
        self.acquired = 0
        self.refreshed = 0

    async def acquire(self, credentials: str, context: AuthContext[Any]) -> MailSession:
        self.acquired += 1
        return MailSession(token=(await context.sdk.login.token()).token)

    async def refresh(self, session: MailSession, context: AuthContext[Any]) -> MailSession:
        self.refreshed += 1
        return MailSession(token=(await context.sdk.login.token()).token)


class MailSdk(AsyncRoot):
    login = api_group(LoginApi)
    mail = api_group(MailApi)


def _sdk(site: MailSite, service: MailService, *, auth_retries: int = 1) -> MailSdk:
    auth = MAIL_SESSION.configure(credentials="ada", service=service)
    raw = httpx.AsyncClient(transport=httpx.MockTransport(site), headers={}, cookies={})
    return MailSdk.from_handler(
        handler=AsyncHttpxHandler(raw, owns_client=True),
        base_url=BASE,
        identity=Identity(auth=(auth,)),
        config=ClientConfig(resilience=Resilience(auth_retries=auth_retries)),
    )


@pytest.mark.asyncio
async def test_a_stale_token_is_refreshed_and_the_request_replayed() -> None:
    site, service = MailSite(), MailService()
    async with _sdk(site, service) as sdk:
        assert (await sdk.mail.folders()).body.folders == ["Inbox"]
        site.expire()
        assert (await sdk.mail.folders()).body.folders == ["Inbox"]

    assert (service.acquired, service.refreshed) == (1, 1)
    assert site.seen == [
        "GET /token",
        "GET /api/folders t1",
        "GET /api/folders t1",
        "GET /token",
        "GET /api/folders t3",
    ]


@pytest.mark.asyncio
async def test_the_declared_error_is_raised_once_the_budget_is_spent() -> None:
    site, service = MailSite(always_rejects=True), MailService()
    async with _sdk(site, service) as sdk:
        with pytest.raises(TokenRejected) as raised:
            await sdk.mail.folders()

    assert raised.value.error.status == 403
    assert service.refreshed == 1
    assert [line for line in site.seen if "/api/" in line] == [
        "GET /api/folders t1",
        "GET /api/folders t2",
    ]


@pytest.mark.asyncio
async def test_no_budget_means_no_refresh() -> None:
    site, service = MailSite(always_rejects=True), MailService()
    async with _sdk(site, service, auth_retries=0) as sdk:
        with pytest.raises(TokenRejected):
            await sdk.mail.folders()
    assert service.refreshed == 0


@pytest.mark.asyncio
async def test_an_error_without_the_mark_is_raised_at_once() -> None:
    site, service = MailSite(always_rejects=True), MailService()
    async with _sdk(site, service) as sdk:
        with pytest.raises(PlainRejected):
            await sdk.mail.folders_plain()
    assert service.refreshed == 0


@pytest.mark.asyncio
async def test_an_operation_that_is_not_idempotent_follows_the_rule_of_401() -> None:
    site, service = MailSite(always_rejects=True), MailService()
    async with _sdk(site, service) as sdk:
        with pytest.raises(UnsafeReplayError, match="auth refresh requires an idempotent"):
            await sdk.mail.move()


def test_the_mark_is_read_from_the_class_and_from_the_case() -> None:
    models = default_model_adapters()
    assert error_case(200, TokenRejected, models=models, operation_id="x").reauth
    assert error_case(200, (TokenRefused, TokenRejected), models=models, operation_id="x").reauth
    assert not error_case(200, PlainRejected, models=models, operation_id="x").reauth
    assert Error(200, Json(TokenRefused), reauth=True).reauth


@pytest.mark.asyncio
async def test_the_mark_on_an_operation_with_no_session_is_refused() -> None:
    @dataclass(frozen=True, slots=True, kw_only=True)
    class Open(HttpOperation[Envelope]):
        __http__ = Http.get("/open", errors={200: TokenRejected})

    class OpenApi(AsyncApi):
        open = op(Open)

    class Sdk(AsyncRoot):
        open = api_group(OpenApi)

    raw = httpx.AsyncClient(transport=httpx.MockTransport(MailSite()), headers={}, cookies={})
    sdk = Sdk.from_handler(handler=AsyncHttpxHandler(raw, owns_client=True), base_url=BASE)
    async with sdk:
        with pytest.raises(PlanError, match=r"asks for a session refresh.*no security"):
            await sdk.open.open()
