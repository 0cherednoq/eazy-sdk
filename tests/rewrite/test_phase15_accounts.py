from __future__ import annotations

import ast
import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, cast

import pytest
from pydantic import BaseModel, SecretStr

from eazy_sdk.accounts import (
    AccountCreated,
    AccountDraft,
    AccountIdentifier,
    AccountStatus,
    BridgedSessionAdopter,
    CompleteRegistration,
    MemoryRegistrationStore,
    PendingRegistration,
    RegistrationCancelledError,
    RegistrationConfigurationError,
    RegistrationDeadlineError,
    RegistrationPersistenceError,
    SessionLifecycle,
    VerificationAccepted,
    VerificationChallenge,
    VerificationCycleError,
    VerificationLimitError,
    account_registration,
    session_lifecycle,
    sync_account_registration,
)
from eazy_sdk.accounts.session import (
    MemorySessionStore,
    SessionKey,
    SessionLifecycleConfig,
    SessionRevision,
    StoredSession,
)
from eazy_sdk.auth import Bearer, ExpiresAt, RefreshToken, session_auth, session_cookie


class SignupCredentials(BaseModel):
    email: Annotated[str, AccountIdentifier()]
    password: SecretStr


class AccountProfile(BaseModel):
    first_name: str
    phone: str


class SignupDetails(BaseModel):
    password_confirmation: SecretStr
    accepted_terms: bool


class UserSession(BaseModel):
    access_token: Annotated[SecretStr, Bearer()]
    refresh_token: Annotated[SecretStr, RefreshToken()]
    expires_at: Annotated[datetime, ExpiresAt()]


@dataclass(frozen=True, slots=True, repr=False)
class EncodedCredentials:
    email: str
    password_length: int


class ExplicitCredentialsCodec:
    def __init__(self) -> None:
        self.encoded = 0
        self.decoded = 0

    def encode(self, value: SignupCredentials) -> object:
        self.encoded += 1
        return EncodedCredentials(
            value.email,
            len(value.password.get_secret_value()),
        )

    def decode(self, value: object) -> SignupCredentials:
        self.decoded += 1
        encoded = EncodedCredentials(*value) if isinstance(value, tuple) else value
        if not isinstance(encoded, EncodedCredentials):
            raise TypeError("unexpected encoded credentials")
        return SignupCredentials(
            email=encoded.email,
            password=SecretStr("x" * encoded.password_length),
        )


@dataclass(frozen=True, slots=True)
class BrowserSignupForm:
    email: str
    password: str = field(repr=False)
    password_confirmation: str = field(repr=False)
    first_name: str
    phone: str
    accepted_terms: bool


@dataclass(frozen=True, slots=True)
class BrowserVerifyForm:
    challenge_id: str
    code: str = field(repr=False)


class EmailCode(BaseModel):
    code: SecretStr


@dataclass(slots=True)
class FakeBrowserDriver:
    """UI-like driver: it exposes no EndpointContract/request/response/client objects."""

    signup_calls: list[BrowserSignupForm] = field(default_factory=list)
    verify_calls: list[BrowserVerifyForm] = field(default_factory=list)
    verification_results: list[VerificationAccepted[UserSession]] = field(default_factory=list)
    create_challenge: VerificationChallenge | None = None
    create_session: UserSession | None = None
    remote_calls: int = 0

    async def submit_signup(self, form: BrowserSignupForm) -> AccountCreated[UserSession]:
        self.remote_calls += 1
        self.signup_calls.append(form)
        await asyncio.sleep(0)
        return AccountCreated(
            remote_id="remote-ada",
            verification=self.create_challenge,
            session=self.create_session,
        )

    async def submit_verification(
        self,
        form: BrowserVerifyForm,
    ) -> VerificationAccepted[UserSession]:
        self.verify_calls.append(form)
        await asyncio.sleep(0)
        if not self.verification_results:
            raise ValueError("invalid verification code")
        return self.verification_results.pop(0)

    async def resend(self, challenge: VerificationChallenge) -> VerificationChallenge:
        return VerificationChallenge(f"{challenge.id}-next", challenge.kind)


type Draft = AccountDraft[SignupCredentials, AccountProfile, SignupDetails]


def signup_draft() -> Draft:
    return AccountDraft(
        credentials=SignupCredentials(
            email="ada@example.test",
            password=SecretStr("correct-horse"),
        ),
        profile=AccountProfile(first_name="Ada", phone="+10000000000"),
        details=SignupDetails(
            password_confirmation=SecretStr("correct-horse"),
            accepted_terms=True,
        ),
    )


class BrowserRegistrationService:
    async def create(
        self,
        draft: Draft,
        context: FakeBrowserDriver,
    ) -> AccountCreated[UserSession]:
        return await context.submit_signup(
            BrowserSignupForm(
                email=draft.credentials.email,
                password=draft.credentials.password.get_secret_value(),
                password_confirmation=(draft.details.password_confirmation.get_secret_value()),
                first_name=draft.profile.first_name,
                phone=draft.profile.phone,
                accepted_terms=draft.details.accepted_terms,
            )
        )

    async def verify(
        self,
        account: Any,
        challenge: VerificationChallenge,
        proof: object,
        context: FakeBrowserDriver,
    ) -> VerificationAccepted[UserSession]:
        assert account.status is AccountStatus.PENDING_VERIFICATION
        code = EmailCode.model_validate(proof)
        return await context.submit_verification(
            BrowserVerifyForm(challenge.id, code.code.get_secret_value())
        )

    async def resend(
        self,
        _account: Any,
        challenge: VerificationChallenge,
        context: FakeBrowserDriver,
    ) -> VerificationChallenge:
        return await context.resend(challenge)


class StaticCodeProvider:
    def __init__(self, code: str = "123456") -> None:
        self.code = code

    async def resolve(
        self,
        _challenge: VerificationChallenge,
        _context: FakeBrowserDriver,
    ) -> object:
        return EmailCode(code=SecretStr(self.code))


def session(now: datetime, token: str = "access") -> UserSession:
    return UserSession(
        access_token=SecretStr(token),
        refresh_token=SecretStr(f"refresh-{token}"),
        expires_at=now + timedelta(hours=1),
    )


def registration(
    driver: FakeBrowserDriver,
    store: MemoryRegistrationStore[
        SignupCredentials,
        AccountProfile,
        SignupDetails,
        UserSession,
    ],
    **options: Any,
) -> Any:
    return account_registration(
        SignupCredentials,
        service=BrowserRegistrationService(),
        store=store,
        context_factory=lambda: driver,
        provider="browser.example",
        **options,
    )


def test_documented_http_session_api_fingerprint_is_frozen_before_extraction() -> None:
    auth_parameters = inspect.signature(session_auth).parameters
    cookie_parameters = inspect.signature(session_cookie).parameters

    assert tuple(auth_parameters) == (
        "session_model",
        "credentials",
        "session",
        "service",
        "store",
        "identity",
        "name",
        "clock",
    )
    assert tuple(cookie_parameters) == (
        "cookie_name",
        "credentials",
        "service",
        "store",
        "identity",
        "name",
        "clock",
    )
    assert inspect.signature(Bearer).parameters["prefix"].default == "Bearer "
    assert inspect.signature(ExpiresAt).parameters["leeway"].default == timedelta(seconds=30)
    assert not inspect.signature(RefreshToken).parameters


def test_accounts_public_surface_hides_lifecycle_context_and_revision_plumbing() -> None:
    import eazy_sdk.accounts as accounts_api

    assert set(accounts_api.__all__) == {
        "AccountCreated",
        "AccountDraft",
        "AccountIdentifier",
        "AccountResource",
        "AccountRegistrationService",
        "AccountStatus",
        "BridgedSessionAdopter",
        "CompleteRegistration",
        "CredentialsCodec",
        "ExpiresAt",
        "MemoryRegistrationStore",
        "PendingRegistration",
        "RefreshToken",
        "RegistrationCancelledError",
        "RegistrationConfigurationError",
        "RegistrationConflictError",
        "RegistrationDeadlineError",
        "RegistrationError",
        "RegistrationFlow",
        "RegistrationNotFoundError",
        "RegistrationOutcomeUnknownError",
        "RegistrationPersistenceError",
        "RegistrationResourceConflictError",
        "RegistrationReconciliationRequiredError",
        "RegistrationAttempt",
        "SessionBridge",
        "SessionConfigurationError",
        "SessionLifecycle",
        "SessionLifecycleError",
        "StoredAccount",
        "SyncRegistrationFlow",
        "VerificationAccepted",
        "VerificationChallenge",
        "VerificationCycleError",
        "VerificationLimitError",
        "VerificationProvider",
        "account_registration",
        "session_lifecycle",
        "sync_account_registration",
    }
    for hidden in (
        "LifecycleGraph",
        "SessionKey",
        "SessionLifecycleConfig",
        "SessionRecord",
        "SessionRevision",
    ):
        assert not hasattr(accounts_api, hidden)


def test_account_core_import_graph_has_no_http_or_browser_dependencies() -> None:
    forbidden = {
        "eazy_sdk.request",
        "eazy_sdk.response",
        "eazy_sdk.clients",
        "eazy_sdk.adapters",
        "httpx",
        "requests",
        "curl_cffi",
        "wreq",
        "playwright",
        "selenium",
    }
    root = Path(__file__).parents[2] / "eazy_sdk" / "accounts"
    for path in root.glob("*.py"):
        if path.name == "http.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            name == blocked or name.startswith(f"{blocked}.")
            for name in imported
            for blocked in forbidden
        ), path


async def test_transport_neutral_session_lifecycle_acquires_reuses_and_adopts() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    store: MemorySessionStore[UserSession] = MemorySessionStore()

    class Service:
        calls = 0

        async def acquire(self, credentials: str, context: str) -> UserSession:
            self.calls += 1
            assert credentials == "browser-credentials"
            assert context == "browser-page"
            return session(now, "one")

    service = Service()
    lifecycle: SessionLifecycle[str, UserSession, str] = SessionLifecycle(
        SessionLifecycleConfig(
            key=SessionKey("browser:ada"),
            context_factory=lambda _graph: "browser-page",
            store=store,
            validate=lambda value: value.expires_at > now,
            parse=UserSession.model_validate,
            credentials="browser-credentials",
            acquire=service,
        )
    )

    first, second = await asyncio.gather(lifecycle.resolve(), lifecycle.resolve())
    adopted = await lifecycle.adopt(session(now, "created-account"))

    assert service.calls == 1
    assert first.revision == second.revision == SessionRevision(1)
    assert adopted.revision == SessionRevision(2)
    assert await store.load(SessionKey("browser:ada")) == StoredSession(
        session(now, "created-account"),
        SessionRevision(2),
    )


async def test_high_level_standalone_session_factory_uses_neutral_annotations() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)

    class Service:
        async def acquire(self, credentials: str, context: str) -> UserSession:
            assert (credentials, context) == ("browser-login", "browser-page")
            return session(now, "standalone")

    lifecycle = session_lifecycle(
        UserSession,
        credentials="browser-login",
        service=Service(),
        context_factory=lambda _graph: "browser-page",
        clock=lambda: now,
    )

    resolved = await lifecycle.resolve()

    assert resolved.value.access_token.get_secret_value() == "standalone"


async def test_browser_session_remains_opaque_without_an_explicit_hybrid_bridge() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)

    @dataclass(frozen=True, slots=True)
    class BrowserStorageState:
        cookies: tuple[tuple[str, str], ...]

    class BrowserToHttpSession:
        def convert(self, value: BrowserStorageState) -> UserSession:
            token = dict(value.cookies)["access_token"]
            return session(now, token)

    store: MemorySessionStore[UserSession] = MemorySessionStore()
    lifecycle: SessionLifecycle[object, UserSession, None] = SessionLifecycle(
        SessionLifecycleConfig(
            key=SessionKey("hybrid:ada"),
            context_factory=lambda _graph: None,
            store=store,
            validate=lambda value: value.expires_at > now,
            parse=UserSession.model_validate,
        )
    )
    bridge = BridgedSessionAdopter(BrowserToHttpSession(), lifecycle)

    await bridge.adopt(BrowserStorageState((("access_token", "from-browser"),)))

    stored = await store.load(SessionKey("hybrid:ada"))
    assert stored is not None
    assert stored.value.access_token.get_secret_value() == "from-browser"


async def test_browser_create_maps_domain_models_persists_and_hands_off_session() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    created_session = session(now)
    driver = FakeBrowserDriver(create_session=created_session)
    codec = ExplicitCredentialsCodec()
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(codec)
    session_store: MemorySessionStore[UserSession] = MemorySessionStore()
    lifecycle: SessionLifecycle[object, UserSession, None] = SessionLifecycle(
        SessionLifecycleConfig(
            key=SessionKey("account:ada"),
            context_factory=lambda _graph: None,
            store=session_store,
            validate=lambda value: value.expires_at > now,
            parse=UserSession.model_validate,
        )
    )

    result = await registration(
        driver,
        store,
        session_lifecycle=lifecycle,
    ).create(signup_draft())

    assert isinstance(result, CompleteRegistration)
    assert result.account.status is AccountStatus.ACTIVE
    assert result.account.identifier == "ada@example.test"
    assert result.account.profile.first_name == "Ada"
    assert codec.encoded == 1
    assert driver.signup_calls == [
        BrowserSignupForm(
            email="ada@example.test",
            password="correct-horse",
            password_confirmation="correct-horse",
            first_name="Ada",
            phone="+10000000000",
            accepted_terms=True,
        )
    ]
    stored = await session_store.load(SessionKey("account:ada"))
    assert stored == StoredSession(created_session, SessionRevision(1))
    assert "correct-horse" not in repr(signup_draft())
    assert "correct-horse" not in repr(result)


async def test_memory_store_owns_a_snapshot_of_profile_and_returned_account() -> None:
    draft = signup_draft()
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())

    result = await registration(FakeBrowserDriver(), store).create(draft)
    assert isinstance(result, CompleteRegistration)

    draft.profile.first_name = "changed outside the store"
    result.account.profile.first_name = "changed returned value"
    stored = await store.load_account("ada@example.test", "browser.example")

    assert stored is not None
    assert stored.profile.first_name == "Ada"


async def test_pending_registration_resumes_and_verifies_after_process_restart() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    challenge = VerificationChallenge("email-1", "email_code", "a***@example.test")
    driver = FakeBrowserDriver(create_challenge=challenge)
    driver.verification_results.append(VerificationAccepted(session=session(now)))
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())

    pending = await registration(driver, store).create(signup_draft())
    assert isinstance(pending, PendingRegistration)
    assert pending.account.status is AccountStatus.PENDING_VERIFICATION

    restarted = registration(driver, store)
    restored = await restarted.resume("ada@example.test")
    assert isinstance(restored, PendingRegistration)
    complete = await restarted.verify(restored, EmailCode(code=SecretStr("123456")))

    assert isinstance(complete, CompleteRegistration)
    assert complete.account.status is AccountStatus.ACTIVE
    assert complete.account.challenge is None
    assert driver.verify_calls == [BrowserVerifyForm("email-1", "123456")]


async def test_automatic_multistep_verification_is_bounded_and_detects_cycles() -> None:
    email = VerificationChallenge("email-1", "email_code")
    sms = VerificationChallenge("sms-1", "sms_code")
    driver = FakeBrowserDriver(create_challenge=email)
    driver.verification_results.extend(
        [
            VerificationAccepted(next_verification=sms),
            VerificationAccepted(),
        ]
    )
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    result = await registration(
        driver,
        store,
        verification={"email_code": StaticCodeProvider(), "sms_code": StaticCodeProvider()},
        max_verification_steps=2,
    ).create(signup_draft())
    assert isinstance(result, CompleteRegistration)

    limited_driver = FakeBrowserDriver(create_challenge=email)
    limited_driver.verification_results.append(VerificationAccepted(next_verification=sms))
    limited_store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    with pytest.raises(VerificationLimitError):
        await registration(
            limited_driver,
            limited_store,
            verification={"email_code": StaticCodeProvider(), "sms_code": StaticCodeProvider()},
            max_verification_steps=1,
        ).create(signup_draft())

    cycle_driver = FakeBrowserDriver(create_challenge=email)
    cycle_driver.verification_results.append(VerificationAccepted(next_verification=email))
    cycle_store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    with pytest.raises(VerificationCycleError):
        await registration(
            cycle_driver,
            cycle_store,
            verification={"email_code": StaticCodeProvider()},
        ).create(signup_draft())


async def test_api_and_proof_failures_do_not_commit_invalid_transitions() -> None:
    driver = FakeBrowserDriver(create_challenge=VerificationChallenge("email-1", "email_code"))
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    flow = registration(driver, store)
    pending = await flow.create(signup_draft())
    assert isinstance(pending, PendingRegistration)

    with pytest.raises(ValueError, match="invalid verification code"):
        await flow.verify(pending, EmailCode(code=SecretStr("wrong")))

    persisted = await store.load_pending("ada@example.test", "browser.example")
    assert persisted == pending
    assert store.commits == [
        "registration.started",
        "registration.remote_created",
        "verification.requested",
    ]


async def test_create_failure_records_failed_state_and_resend_failure_preserves_pending() -> None:
    class RejectedCreateDriver(FakeBrowserDriver):
        async def submit_signup(
            self,
            form: BrowserSignupForm,
        ) -> AccountCreated[UserSession]:
            self.remote_calls += 1
            self.signup_calls.append(form)
            raise ValueError("registration rejected")

    rejected = RejectedCreateDriver()
    empty_store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    with pytest.raises(ValueError, match="registration rejected"):
        await registration(rejected, empty_store).create(signup_draft())
    failed = await empty_store.load_account("ada@example.test", "browser.example")
    assert failed is not None and failed.status is AccountStatus.REGISTRATION_FAILED
    assert empty_store.commits == ["registration.started", "registration.failed"]

    class RejectedResendDriver(FakeBrowserDriver):
        async def resend(self, _challenge: VerificationChallenge) -> VerificationChallenge:
            raise ValueError("resend rejected")

    resend_driver = RejectedResendDriver(
        create_challenge=VerificationChallenge("email-1", "email_code")
    )
    pending_store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    flow = registration(resend_driver, pending_store)
    pending = await flow.create(signup_draft())
    assert isinstance(pending, PendingRegistration)

    with pytest.raises(ValueError, match="resend rejected"):
        await flow.resend(pending)

    assert await pending_store.load_pending("ada@example.test", "browser.example") == pending
    assert pending_store.commits == [
        "registration.started",
        "registration.remote_created",
        "verification.requested",
    ]


async def test_remote_success_local_failure_is_reconciliation_not_retry() -> None:
    class FailingStore(
        MemoryRegistrationStore[
            SignupCredentials,
            AccountProfile,
            SignupDetails,
            UserSession,
        ]
    ):
        async def record_remote_created(self, *_args: Any, **_kwargs: Any) -> Any:
            raise OSError("database unavailable")

    driver = FakeBrowserDriver()
    flow = registration(driver, FailingStore(ExplicitCredentialsCodec()))

    with pytest.raises(RegistrationPersistenceError) as captured:
        await flow.create(signup_draft())

    assert captured.value.remote_id == "remote-ada"
    assert driver.remote_calls == 1


async def test_failed_session_handoff_is_reconciled_from_committed_account() -> None:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    created_session = session(now)
    driver = FakeBrowserDriver(create_session=created_session)
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())

    class FailingAdopter:
        async def adopt(self, _value: UserSession) -> object:
            raise OSError("session store unavailable")

    with pytest.raises(RegistrationPersistenceError) as captured:
        await registration(
            driver,
            store,
            session_lifecycle=FailingAdopter(),
        ).create(signup_draft())

    assert captured.value.event == "session.adopted"
    persisted = await store.load_account("ada@example.test", "browser.example")
    assert persisted is not None
    assert persisted.status is AccountStatus.ACTIVE
    assert persisted.session == created_session

    adopted: list[UserSession] = []

    class RecordingAdopter:
        async def adopt(self, value: UserSession) -> object:
            adopted.append(value)
            return value

    result = await registration(
        driver,
        store,
        session_lifecycle=RecordingAdopter(),
    ).resume("ada@example.test")

    assert isinstance(result, CompleteRegistration)
    assert adopted == [created_session]
    assert driver.remote_calls == 1


async def test_same_identifier_create_is_singleflight_and_returns_one_account() -> None:
    driver = FakeBrowserDriver()
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    flow = registration(driver, store)

    first, second = await asyncio.gather(
        flow.create(signup_draft()),
        flow.create(signup_draft()),
    )

    assert isinstance(first, CompleteRegistration)
    assert isinstance(second, CompleteRegistration)
    assert first.account.id == second.account.id
    assert driver.remote_calls == 1


def test_sync_runner_uses_the_same_registration_flow() -> None:
    driver = FakeBrowserDriver(
        create_challenge=VerificationChallenge("email-1", "email_code"),
        verification_results=[VerificationAccepted()],
    )
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    with sync_account_registration(
        SignupCredentials,
        service=BrowserRegistrationService(),
        store=store,
        context_factory=lambda: driver,
        provider="browser.example",
    ) as flow:
        pending = flow.create(signup_draft())
        assert isinstance(pending, PendingRegistration)
        result = flow.verify(pending, EmailCode(code=SecretStr("123456")))

    assert isinstance(result, CompleteRegistration)
    assert result.account.identifier == "ada@example.test"
    assert driver.remote_calls == 1
    assert driver.verify_calls == [BrowserVerifyForm("email-1", "123456")]


async def test_resend_commits_only_the_replacement_challenge() -> None:
    driver = FakeBrowserDriver(create_challenge=VerificationChallenge("email-1", "email_code"))
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())
    flow = registration(driver, store)
    pending = await flow.create(signup_draft())
    assert isinstance(pending, PendingRegistration)

    resent = await flow.resend(pending)

    assert resent.challenge.id == "email-1-next"
    restored = await store.load_pending("ada@example.test", "browser.example")
    assert restored == resent


async def test_configuration_deadline_and_cancellation_fail_before_remote_io() -> None:
    class NoIdentifier(BaseModel):
        email: str

    driver = FakeBrowserDriver()
    store: MemoryRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = MemoryRegistrationStore(ExplicitCredentialsCodec())

    with pytest.raises(RegistrationConfigurationError, match="exactly one"):
        account_registration(
            cast(type[SignupCredentials], NoIdentifier),
            service=BrowserRegistrationService(),
            store=store,
            context_factory=lambda: driver,
        )

    now = datetime(2026, 8, 15, tzinfo=UTC)
    with pytest.raises(RegistrationDeadlineError):
        await registration(
            driver,
            store,
            deadline=now,
            clock=lambda: now,
        ).create(signup_draft())
    with pytest.raises(RegistrationCancelledError):
        await registration(
            driver,
            store,
            cancelled=lambda: True,
        ).create(signup_draft())
    assert driver.remote_calls == 0
