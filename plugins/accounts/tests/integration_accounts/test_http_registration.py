from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
import pytest
from eazy_sdk_accounts import (
    AccountCreated,
    AccountDraft,
    AccountIdentifier,
    AccountStatus,
    CompleteRegistration,
    MemoryRegistrationStore,
    PendingRegistration,
    SessionLifecycle,
    VerificationAccepted,
    VerificationChallenge,
    account_registration,
)
from eazy_sdk_accounts.http import HttpRegistrationContext
from eazy_sdk_accounts.registration import StoredAccount
from pydantic import BaseModel, SecretStr
from pytest_httpserver import HTTPServer

from eazy_sdk import (
    AsyncApi,
    ClientConfig,
    RetryPolicy,
    UnsafeReplayError,
    api,
)
from eazy_sdk.auth import Bearer, ExpiresAt, RefreshToken, session_cookie
from eazy_sdk.auth.cookies import HttpCookieSession, parse_session_cookie
from eazy_sdk.auth.session import MemorySessionStore, SessionKey, SessionLifecycleConfig
from eazy_sdk.request import (
    JsonBody,
    SigningKey,
    SigningKeyRequirement,
    header_output,
    hmac_sha256,
    method,
)
from eazy_sdk.response import Json, Responses, Success
from tests._support.zapros_clients import client_from_httpx


class SignupCredentials(BaseModel):
    email: Annotated[str, AccountIdentifier()]
    password: SecretStr


class AccountProfile(BaseModel):
    first_name: str


class SignupDetails(BaseModel):
    accepted_terms: bool


class RegisterRequest(BaseModel):
    email: str
    password: str
    first_name: str
    accepted_terms: bool


class UserSession(BaseModel):
    access_token: Annotated[SecretStr, Bearer()]
    refresh_token: Annotated[SecretStr, RefreshToken()]
    expires_at: Annotated[datetime, ExpiresAt()]


class RegisterResponse(BaseModel):
    user_id: str
    session: UserSession


class PendingRegisterResponse(BaseModel):
    user_id: str
    verification_id: str


class VerifyRequest(BaseModel):
    verification_id: str
    code: str


class VerifyResponse(BaseModel):
    session: UserSession


class VerificationCode(BaseModel):
    code: SecretStr


SIGNATURE = hmac_sha256(
    key=SigningKeyRequirement("registration"),
    base=method(),
    output=header_output("X-Registration-Signature"),
)


class CookieRegisterResponse(BaseModel):
    user_id: str


class RegistrationApi(AsyncApi):
    @api.post(
        "/accounts",
        operation_id="createAccount",
        responses=Responses(success=(Success(201, Json(RegisterResponse)),)),
        signing=(SIGNATURE,),
        idempotent=True,
    )
    async def create_account(
        self, *, body: Annotated[RegisterRequest, JsonBody()]
    ) -> RegisterResponse:
        raise NotImplementedError

    @api.post(
        "/cookie-accounts",
        operation_id="createCookieAccount",
        responses=Responses(success=(Success(201, Json(CookieRegisterResponse)),)),
    )
    async def create_cookie_account(
        self, *, body: Annotated[RegisterRequest, JsonBody()]
    ) -> CookieRegisterResponse:
        raise NotImplementedError

    @api.post(
        "/accounts/pending",
        operation_id="createPendingAccount",
        responses=Responses(success=(Success(202, Json(PendingRegisterResponse)),)),
    )
    async def create_pending_account(
        self, *, body: Annotated[RegisterRequest, JsonBody()]
    ) -> PendingRegisterResponse:
        raise NotImplementedError

    @api.post(
        "/accounts/verify",
        operation_id="verifyAccount",
        responses=Responses(success=(Success(200, Json(VerifyResponse)),)),
    )
    async def verify_account(self, *, body: Annotated[VerifyRequest, JsonBody()]) -> VerifyResponse:
        raise NotImplementedError


class RegistrationSdk:
    def __init__(self, client: Any) -> None:
        self.registration = RegistrationApi(client)


class CredentialsCodec:
    def encode(self, value: SignupCredentials) -> object:
        return {"email": value.email, "password": "encrypted-by-application"}

    def decode(self, value: object) -> SignupCredentials:
        if not isinstance(value, dict):
            raise TypeError("invalid credentials payload")
        return SignupCredentials(
            email=str(value["email"]),
            password=SecretStr("restored-by-application"),
        )


class RegistrationService:
    async def create(
        self,
        draft: AccountDraft[SignupCredentials, AccountProfile, SignupDetails],
        context: HttpRegistrationContext[RegistrationSdk],
    ) -> AccountCreated[UserSession]:
        result = await context.sdk.registration.create_account(
            body=RegisterRequest(
                email=draft.credentials.email,
                password=draft.credentials.password.get_secret_value(),
                first_name=draft.profile.first_name,
                accepted_terms=draft.details.accepted_terms,
            )
        )
        return AccountCreated(remote_id=result.user_id, session=result.session)

    async def verify(
        self,
        _account: StoredAccount[AccountProfile, UserSession],
        _challenge: VerificationChallenge,
        _proof: object,
        _context: HttpRegistrationContext[RegistrationSdk],
    ) -> VerificationAccepted[UserSession]:
        raise AssertionError("this flow has no verification step")


class CookieRegistrationService:
    def __init__(self, now: datetime) -> None:
        self._now = now

    async def create(
        self,
        draft: AccountDraft[SignupCredentials, AccountProfile, SignupDetails],
        context: HttpRegistrationContext[RegistrationSdk],
    ) -> AccountCreated[HttpCookieSession]:
        result = context.capture(
            await context.sdk.registration.create_cookie_account.with_response(
                body=RegisterRequest(
                    email=draft.credentials.email,
                    password=draft.credentials.password.get_secret_value(),
                    first_name=draft.profile.first_name,
                    accepted_terms=draft.details.accepted_terms,
                )
            )
        )
        cookie = parse_session_cookie(
            context.responses,
            "session_id",
            now=self._now,
        )
        return AccountCreated(remote_id=result.user_id, session=cookie)

    async def verify(
        self,
        _account: StoredAccount[AccountProfile, HttpCookieSession],
        _challenge: VerificationChallenge,
        _proof: object,
        _context: HttpRegistrationContext[RegistrationSdk],
    ) -> VerificationAccepted[HttpCookieSession]:
        raise AssertionError("this flow has no verification step")


class LocalhostRegistrationService:
    async def create(
        self,
        draft: AccountDraft[SignupCredentials, AccountProfile, SignupDetails],
        context: HttpRegistrationContext[RegistrationSdk],
    ) -> AccountCreated[UserSession]:
        response = await context.sdk.registration.create_pending_account(
            body=RegisterRequest(
                email=draft.credentials.email,
                password=draft.credentials.password.get_secret_value(),
                first_name=draft.profile.first_name,
                accepted_terms=draft.details.accepted_terms,
            )
        )
        return AccountCreated(
            remote_id=response.user_id,
            verification=VerificationChallenge(
                response.verification_id,
                "email_code",
            ),
        )

    async def verify(
        self,
        _account: StoredAccount[AccountProfile, UserSession],
        challenge: VerificationChallenge,
        proof: object,
        context: HttpRegistrationContext[RegistrationSdk],
    ) -> VerificationAccepted[UserSession]:
        code = VerificationCode.model_validate(proof)
        response = await context.sdk.registration.verify_account(
            body=VerifyRequest(
                verification_id=challenge.id,
                code=code.code.get_secret_value(),
            )
        )
        return VerificationAccepted(session=response.session)


async def test_http_registration_uses_existing_executor_signing_and_session_lifecycle() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    observed: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.headers["X-Registration-Signature"])
        assert request.url.path == "/accounts"
        assert json.loads(request.content) == {
            "email": "ada@example.test",
            "password": "correct-horse",
            "first_name": "Ada",
            "accepted_terms": True,
        }
        return httpx.Response(
            201,
            json={
                "user_id": "remote-ada",
                "session": {
                    "access_token": "created-access",
                    "refresh_token": "created-refresh",
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                },
            },
        )

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            key_provider=lambda _requirement: SigningKey("signing-secret"),
        ),
    )
    registration_store: MemoryRegistrationStore[
        SignupCredentials,
        AccountProfile,
        SignupDetails,
        UserSession,
    ] = MemoryRegistrationStore(CredentialsCodec())
    session_store: MemorySessionStore[UserSession] = MemorySessionStore()
    session_lifecycle: SessionLifecycle[object, UserSession, None] = SessionLifecycle(
        SessionLifecycleConfig(
            key=SessionKey("account:ada"),
            context_factory=lambda _graph: None,
            store=session_store,
            validate=lambda value: value.expires_at > now,
            parse=UserSession.model_validate,
        )
    )
    flow = account_registration(
        SignupCredentials,
        service=RegistrationService(),
        store=registration_store,
        context_factory=lambda: HttpRegistrationContext(RegistrationSdk(client)),
        session_lifecycle=session_lifecycle,
    )
    result = await flow.create(
        AccountDraft(
            credentials=SignupCredentials(
                email="ada@example.test",
                password=SecretStr("correct-horse"),
            ),
            profile=AccountProfile(first_name="Ada"),
            details=SignupDetails(accepted_terms=True),
        )
    )
    await client.aclose()

    assert isinstance(result, CompleteRegistration)
    assert result.account.remote_id == "remote-ada"
    assert len(observed) == 1
    adopted = await session_store.load(SessionKey("account:ada"))
    assert adopted is not None
    assert adopted.value.access_token.get_secret_value() == "created-access"


async def test_registration_cookie_session_is_adopted_without_cookie_annotations() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    fallback_login_calls = 0
    protected_cookies: list[str] = []

    class FallbackCookieLogin:
        async def acquire(
            self,
            _credentials: SignupCredentials,
            _context: object,
        ) -> object:
            nonlocal fallback_login_calls
            fallback_login_calls += 1
            raise AssertionError("registration session should skip fallback login")

    auth = session_cookie(
        "session_id",
        credentials=signup_credentials(),
        service=FallbackCookieLogin(),
        clock=lambda: now,
    )

    class ProtectedApi(AsyncApi):
        @api.get(
            "/protected",
            operation_id="protectedAfterRegistration",
            responses=Responses(success=(Success(200, Json(CookieRegisterResponse)),)),
            security=auth.scheme,
        )
        async def protected(self) -> CookieRegisterResponse:
            raise NotImplementedError

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cookie-accounts":
            return httpx.Response(
                201,
                json={"user_id": "remote-cookie"},
                headers={"Set-Cookie": "session_id=created-cookie; Path=/; Max-Age=3600; HttpOnly"},
            )
        protected_cookies.append(request.headers.get("Cookie", ""))
        return httpx.Response(200, json={"user_id": "remote-cookie"})

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth),
    )
    store: MemoryRegistrationStore[
        SignupCredentials,
        AccountProfile,
        SignupDetails,
        HttpCookieSession,
    ] = MemoryRegistrationStore(CredentialsCodec())
    flow = account_registration(
        SignupCredentials,
        service=CookieRegistrationService(now),
        store=store,
        context_factory=lambda: HttpRegistrationContext(RegistrationSdk(client)),
        session_lifecycle=auth,
    )

    result = await flow.create(signup_draft())
    response = await ProtectedApi(client).protected()
    await client.aclose()

    assert isinstance(result, CompleteRegistration)
    assert response.user_id == "remote-cookie"
    assert protected_cookies == ["session_id=created-cookie"]
    assert fallback_login_calls == 0


async def test_localhost_create_verify_and_session_handoff(
    httpserver: HTTPServer,
) -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    httpserver.expect_oneshot_request(
        "/accounts/pending",
        method="POST",
        json={
            "email": "ada@example.test",
            "password": "correct-horse",
            "first_name": "Ada",
            "accepted_terms": True,
        },
    ).respond_with_json(
        {"user_id": "remote-ada", "verification_id": "email-1"},
        status=202,
    )
    httpserver.expect_oneshot_request(
        "/accounts/verify",
        method="POST",
        json={"verification_id": "email-1", "code": "123456"},
    ).respond_with_json(
        {
            "session": {
                "access_token": "verified-access",
                "refresh_token": "verified-refresh",
                "expires_at": (now + timedelta(hours=1)).isoformat(),
            }
        }
    )

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url=httpserver.url_for("/"),
            headers={},
            cookies={},
        )
    )
    registration_store: MemoryRegistrationStore[
        SignupCredentials,
        AccountProfile,
        SignupDetails,
        UserSession,
    ] = MemoryRegistrationStore(CredentialsCodec())
    session_store: MemorySessionStore[UserSession] = MemorySessionStore()
    lifecycle: SessionLifecycle[object, UserSession, None] = SessionLifecycle(
        SessionLifecycleConfig(
            key=SessionKey("account:localhost-ada"),
            context_factory=lambda _graph: None,
            store=session_store,
            validate=lambda value: value.expires_at > now,
            parse=UserSession.model_validate,
        )
    )
    flow = account_registration(
        SignupCredentials,
        service=LocalhostRegistrationService(),
        store=registration_store,
        context_factory=lambda: HttpRegistrationContext(RegistrationSdk(client)),
        session_lifecycle=lifecycle,
    )

    pending = await flow.create(signup_draft())
    assert isinstance(pending, PendingRegistration)
    complete = await flow.verify(
        pending,
        VerificationCode(code=SecretStr("123456")),
    )
    await client.aclose()

    assert isinstance(complete, CompleteRegistration)
    assert complete.account.status.value == "active"
    adopted = await session_store.load(SessionKey("account:localhost-ada"))
    assert adopted is not None
    assert adopted.value.access_token.get_secret_value() == "verified-access"
    httpserver.check()


async def test_unsafe_localhost_create_is_not_retried_or_persisted(
    httpserver: HTTPServer,
) -> None:
    httpserver.expect_oneshot_request(
        "/accounts/pending",
        method="POST",
    ).respond_with_json({"error": "busy"}, status=503)
    client = client_from_httpx(
        httpx.AsyncClient(
            base_url=httpserver.url_for("/"),
            headers={},
            cookies={},
        ),
        config=ClientConfig(
            retry=RetryPolicy.safe(max_attempts=2),
            auth_retries=0,
        ),
    )
    store: MemoryRegistrationStore[
        SignupCredentials,
        AccountProfile,
        SignupDetails,
        UserSession,
    ] = MemoryRegistrationStore(CredentialsCodec())
    flow = account_registration(
        SignupCredentials,
        service=LocalhostRegistrationService(),
        store=store,
        context_factory=lambda: HttpRegistrationContext(RegistrationSdk(client)),
    )

    with pytest.raises(UnsafeReplayError, match="idempotent"):
        await flow.create(signup_draft())
    await client.aclose()

    failed = await store.load_account("ada@example.test", None)
    assert failed is not None and failed.status is AccountStatus.REGISTRATION_FAILED
    httpserver.check()


def signup_credentials() -> SignupCredentials:
    return SignupCredentials(
        email="ada@example.test",
        password=SecretStr("correct-horse"),
    )


def signup_draft() -> AccountDraft[SignupCredentials, AccountProfile, SignupDetails]:
    return AccountDraft(
        credentials=signup_credentials(),
        profile=AccountProfile(first_name="Ada"),
        details=SignupDetails(accepted_terms=True),
    )
