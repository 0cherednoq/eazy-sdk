from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import pytest
from eazy_sdk_accounts import (
    AccountCreated,
    AccountDraft,
    AccountIdentifier,
    AccountResource,
    AccountStatus,
    CompleteRegistration,
    RegistrationCancelledError,
    RegistrationOutcomeUnknownError,
    RegistrationReconciliationRequiredError,
    RegistrationResourceConflictError,
    StoredAccount,
    VerificationAccepted,
    VerificationChallenge,
    account_registration,
)
from eazy_sdk_sqlmodel import (
    SqlRegistrationStore,
    SqlRegistrationTransactionError,
    SqlResourceConflictError,
    SqlSessionStore,
    build_sqlmodel_storage,
)
from eazy_sdk_sqlmodel.tables import Account, AccountEvent, AccountLink, Session, Verification
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from eazy_sdk.auth.session import SessionKey


class SignupCredentials(BaseModel):
    email: Annotated[str, AccountIdentifier()]
    password: SecretStr


class AccountProfile(BaseModel):
    first_name: str


class SignupDetails(BaseModel):
    accepted_terms: bool


class UserSession(BaseModel):
    access_token: SecretStr
    expires_at: datetime


class UnsupportedCredentials(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    email: str
    payload: object


def draft(
    email: str = "ada@example.test",
) -> AccountDraft[SignupCredentials, AccountProfile, SignupDetails]:
    return AccountDraft(
        credentials=SignupCredentials(email=email, password=SecretStr("correct-horse")),
        profile=AccountProfile(first_name="Ada"),
        details=SignupDetails(accepted_terms=True),
    )


def store(
    session: AsyncSession,
) -> SqlRegistrationStore[SignupCredentials, AccountProfile, SignupDetails, UserSession]:
    return SqlRegistrationStore(
        session,
        credentials_model=SignupCredentials,
        profile_model=AccountProfile,
        session_model=UserSession,
    )


async def test_begin_happens_before_remote_result_and_normalizes_columns(
    session: AsyncSession,
) -> None:
    registration = store(session)
    attempt = await registration.begin_registration(
        identifier="ada@example.test",
        provider=None,
        draft=draft(),
        resources=(),
        correlation_id="attempt-1",
    )
    row = (await session.execute(select(Account))).scalars().one()
    assert attempt.account.status is AccountStatus.PROVISIONING
    assert row.status == "provisioning"
    assert row.revision == 0
    assert row.profile["data"] == {"first_name": "Ada"}
    assert row.credentials["data"]["password"] == "correct-horse"
    assert [event.type for event in (await session.execute(select(AccountEvent))).scalars()] == [
        "registration.started"
    ]


async def test_unsupported_credentials_fail_before_partial_database_write(
    session: AsyncSession,
) -> None:
    registration: SqlRegistrationStore[
        UnsupportedCredentials, AccountProfile, SignupDetails, UserSession
    ] = SqlRegistrationStore(
        session,
        credentials_model=UnsupportedCredentials,
        profile_model=AccountProfile,
        session_model=UserSession,
    )
    unsupported = object()
    with pytest.raises(TypeError, match="unsupported value"):
        await registration.begin_registration(
            identifier="unsupported@example.test",
            provider=None,
            draft=AccountDraft(
                credentials=UnsupportedCredentials(
                    email="unsupported@example.test",
                    payload=unsupported,
                ),
                profile=AccountProfile(first_name="Unsupported"),
                details=SignupDetails(accepted_terms=True),
            ),
            resources=(),
            correlation_id="unsupported",
        )
    assert not (await session.execute(select(Account))).scalars().all()


async def test_create_verify_session_and_events_are_atomic(session: AsyncSession) -> None:
    registration = store(session)
    attempt = await registration.begin_registration(
        identifier="ada@example.test",
        provider="example.test",
        draft=draft(),
        resources=(),
        correlation_id="attempt-2",
    )
    challenge = VerificationChallenge("email-1", "email_code", "a***@example.test")
    created = await registration.record_remote_created(
        attempt,
        AccountCreated(remote_id="remote-ada", verification=challenge),
    )
    assert created.status is AccountStatus.PENDING_VERIFICATION
    assert created.challenge == challenge
    assert await registration.load_credentials("ada@example.test", "example.test") == (
        draft().credentials
    )

    accepted_session = UserSession(
        access_token=SecretStr("created-access"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    complete = await registration.commit_verification(
        created,
        challenge,
        VerificationAccepted(session=accepted_session),
    )
    assert complete.status is AccountStatus.ACTIVE
    assert complete.session == accepted_session
    assert complete.revision == 2
    assert len((await session.execute(select(Account))).scalars().all()) == 1
    assert len((await session.execute(select(Verification))).scalars().all()) == 1
    assert len((await session.execute(select(Session))).scalars().all()) == 1
    neutral_store = SqlSessionStore(
        session,
        UUID(complete.id),
        session_model=UserSession,
        kind="registration",
    )
    neutral_session = await neutral_store.load(SessionKey("registration"))
    assert neutral_session is not None and neutral_session.value == accepted_session
    types = [event.type for event in (await session.execute(select(AccountEvent))).scalars()]
    assert types == [
        "registration.started",
        "registration.remote_created",
        "verification.requested",
        "verification.accepted",
        "account.activated",
        "session.created",
    ]


async def test_resend_preserves_history_and_via_account(session: AsyncSession) -> None:
    mailbox = Account(provider="mail", identifier="mail@example.test", status="active")
    session.add(mailbox)
    await session.flush()
    registration = store(session)
    initial = VerificationChallenge(
        "email-1",
        "email_code",
        via_account_id=str(mailbox.id),
    )
    attempt = await registration.begin_registration(
        identifier="ada@example.test",
        provider=None,
        draft=draft(),
        resources=(),
        correlation_id="attempt-3",
    )
    account = await registration.record_remote_created(
        attempt,
        AccountCreated(verification=initial),
    )
    replacement = VerificationChallenge(
        "email-2",
        "email_code",
        via_account_id=str(mailbox.id),
    )
    account = await registration.commit_resend(account, initial, replacement)
    rows = list((await session.execute(select(Verification))).scalars().all())
    rows.sort(key=lambda row: row.created_at)
    assert [row.status for row in rows] == ["superseded", "pending"]
    assert rows[1].replaces_id == rows[0].id
    assert rows[1].via_account_id == mailbox.id
    assert account.challenge == replacement


class Service:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    async def create(self, _draft, _context):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("remote rejected")
        return AccountCreated(remote_id=f"remote-{self.calls}")

    async def verify(self, *_args):  # type: ignore[no-untyped-def]
        return VerificationAccepted()


async def test_exclusive_resource_conflict_stops_before_remote_io(session: AsyncSession) -> None:
    mailbox = Account(provider="mail", identifier="mail@example.test", status="active")
    session.add(mailbox)
    await session.flush()
    resource_account: StoredAccount[AccountProfile, UserSession] = StoredAccount(
        id=str(mailbox.id),
        identifier=mailbox.identifier,
        provider="mail",
        remote_id=None,
        profile=AccountProfile(first_name="Mailbox"),
        status=AccountStatus.ACTIVE,
        revision=0,
    )
    resource = AccountResource(account=resource_account)
    first_service = Service()
    first = account_registration(
        SignupCredentials,
        service=first_service,
        store=store(session),
        context_factory=lambda: None,
        provider="site-a",
    )
    first_result = await first.create(draft("first@example.test"), using=(resource,))
    assert isinstance(first_result, CompleteRegistration)
    second_service = Service()
    second = account_registration(
        SignupCredentials,
        service=second_service,
        store=store(session),
        context_factory=lambda: None,
        provider="site-b",
    )
    with pytest.raises(RegistrationResourceConflictError):
        await second.create(draft("second@example.test"), using=(resource,))
    assert second_service.calls == 0


async def test_email_and_phone_reservation_is_atomic_on_conflict(session: AsyncSession) -> None:
    email = Account(provider="mail", identifier="atomic@example.test", status="active")
    phone = Account(provider="phone", identifier="+100000000", status="active")
    competitor = Account(provider="site", identifier="competitor-atomic", status="active")
    session.add_all([email, phone, competitor])
    await session.flush()
    links = build_sqlmodel_storage(session).links
    await links.reserve(
        owner_account_id=competitor.id,
        resource_account_id=phone.id,
        relation="registration_phone",
        exclusive_scope="registration",
    )
    await session.commit()
    email_id = email.id
    resources = (
        AccountResource(
            account=StoredAccount(
                id=str(email.id),
                identifier=email.identifier,
                provider="mail",
                remote_id=None,
                profile=AccountProfile(first_name="Email"),
                status=AccountStatus.ACTIVE,
                revision=0,
            ),
            relation="registration_email",
        ),
        AccountResource(
            account=StoredAccount(
                id=str(phone.id),
                identifier=phone.identifier,
                provider="phone",
                remote_id=None,
                profile=AccountProfile(first_name="Phone"),
                status=AccountStatus.ACTIVE,
                revision=0,
            ),
            relation="registration_phone",
        ),
    )
    service = Service()
    registration_store = store(session)
    flow = account_registration(
        SignupCredentials,
        service=service,
        store=registration_store,
        context_factory=lambda: None,
        provider="atomic-site",
    )
    with pytest.raises(RegistrationResourceConflictError):
        await flow.create(draft(), using=resources)
    assert service.calls == 0
    assert await registration_store.load_account("ada@example.test", "atomic-site") is None
    email_links = (
        (
            await session.execute(
                select(AccountLink).where(AccountLink.resource_account_id == email_id)
            )
        )
        .scalars()
        .all()
    )
    assert email_links == []


async def test_reservation_is_committed_and_visible_when_remote_service_starts(
    session: AsyncSession,
) -> None:
    mailbox = Account(provider="mail", identifier="visible@example.test", status="active")
    competitor = Account(provider="site", identifier="competitor", status="provisioning")
    session.add_all([mailbox, competitor])
    await session.commit()
    resource = AccountResource(
        account=StoredAccount(
            id=str(mailbox.id),
            identifier=mailbox.identifier,
            provider="mail",
            remote_id=None,
            profile=AccountProfile(first_name="Mailbox"),
            status=AccountStatus.ACTIVE,
            revision=0,
        )
    )

    class ObservingService(Service):
        observed_conflict = False

        async def create(self, _draft, _context):  # type: ignore[no-untyped-def]
            self.calls += 1
            async with AsyncSession(session.bind, expire_on_commit=False) as observer:
                links = build_sqlmodel_storage(observer).links
                with pytest.raises(SqlResourceConflictError):
                    await links.reserve(
                        owner_account_id=competitor.id,
                        resource_account_id=mailbox.id,
                        relation="registration_identity",
                        exclusive_scope="registration",
                    )
                await observer.rollback()
            self.observed_conflict = True
            return AccountCreated(remote_id="remote-visible")

    service = ObservingService()
    flow = account_registration(
        SignupCredentials,
        service=service,
        store=store(session),
        context_factory=lambda: None,
        provider="site",
    )
    assert isinstance(await flow.create(draft(), using=(resource,)), CompleteRegistration)
    assert service.observed_conflict


async def test_registration_store_rejects_explicit_outer_transaction(
    session: AsyncSession,
) -> None:
    registration = store(session)
    async with session.begin():
        with pytest.raises(SqlRegistrationTransactionError, match="outer transaction"):
            await registration.begin_registration(
                identifier="ada@example.test",
                provider=None,
                draft=draft(),
                resources=(),
                correlation_id="explicit-outer",
            )


async def test_remote_failure_records_history_releases_and_retry_reuses_account(
    session: AsyncSession,
) -> None:
    mailbox = Account(provider="mail", identifier="retry@example.test", status="active")
    session.add(mailbox)
    await session.flush()
    resource = AccountResource(
        account=StoredAccount(
            id=str(mailbox.id),
            identifier=mailbox.identifier,
            provider="mail",
            remote_id=None,
            profile=AccountProfile(first_name="Mailbox"),
            status=AccountStatus.ACTIVE,
            revision=0,
        )
    )
    service = Service(fail_first=True)
    registration_store = store(session)
    flow = account_registration(
        SignupCredentials,
        service=service,
        store=registration_store,
        context_factory=lambda: None,
        provider="site",
    )
    with pytest.raises(RuntimeError, match="remote rejected"):
        await flow.create(draft(), using=(resource,))
    failed = await registration_store.load_account("ada@example.test", "site")
    assert failed is not None and failed.status is AccountStatus.REGISTRATION_FAILED
    failed_id = failed.id
    link = (await session.execute(select(AccountLink))).scalars().one()
    assert link.status == "released" and link.exclusive_scope is None

    result = await flow.create(draft(), using=(resource,))
    assert isinstance(result, CompleteRegistration)
    assert result.account.id == failed_id
    assert service.calls == 2
    rows = (await session.execute(select(Account))).scalars().all()
    assert len([row for row in rows if row.provider == "site"]) == 1
    event_types = [event.type for event in (await session.execute(select(AccountEvent))).scalars()]
    assert event_types.count("registration.started") == 2
    assert "registration.failed" in event_types
    assert "account.link.released" in event_types


async def test_ambiguous_remote_outcome_requires_reconciliation_and_keeps_reservation(
    session: AsyncSession,
) -> None:
    mailbox = Account(provider="mail", identifier="uncertain@example.test", status="active")
    session.add(mailbox)
    await session.flush()
    resource = AccountResource(
        account=StoredAccount(
            id=str(mailbox.id),
            identifier=mailbox.identifier,
            provider="mail",
            remote_id=None,
            profile=AccountProfile(first_name="Mailbox"),
            status=AccountStatus.ACTIVE,
            revision=0,
        )
    )

    class UncertainService(Service):
        async def create(self, _draft, _context):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise RegistrationOutcomeUnknownError("connection dropped after submit")

    service = UncertainService()
    registration_store = store(session)
    flow = account_registration(
        SignupCredentials,
        service=service,
        store=registration_store,
        context_factory=lambda: None,
        provider="site",
    )
    with pytest.raises(RegistrationReconciliationRequiredError) as captured:
        await flow.create(draft(), using=(resource,))
    assert captured.value.account.status is AccountStatus.RECONCILIATION_REQUIRED
    link = (await session.execute(select(AccountLink))).scalars().one()
    assert link.status == "active" and link.exclusive_scope == "registration"
    with pytest.raises(RegistrationReconciliationRequiredError):
        await flow.create(draft(), using=(resource,))
    assert service.calls == 1


async def test_cancellation_after_reservation_compensates_before_remote_io(
    session: AsyncSession,
) -> None:
    mailbox = Account(provider="mail", identifier="cancel@example.test", status="active")
    session.add(mailbox)
    await session.commit()
    resource = AccountResource(
        account=StoredAccount(
            id=str(mailbox.id),
            identifier=mailbox.identifier,
            provider="mail",
            remote_id=None,
            profile=AccountProfile(first_name="Mailbox"),
            status=AccountStatus.ACTIVE,
            revision=0,
        )
    )
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    service = Service()
    registration_store = store(session)
    flow = account_registration(
        SignupCredentials,
        service=service,
        store=registration_store,
        context_factory=lambda: None,
        provider="site",
        cancelled=cancelled,
    )
    with pytest.raises(RegistrationCancelledError):
        await flow.create(draft(), using=(resource,))
    assert service.calls == 0
    failed = await registration_store.load_account("ada@example.test", "site")
    assert failed is not None and failed.status is AccountStatus.REGISTRATION_FAILED
    link = (await session.execute(select(AccountLink))).scalars().one()
    assert link.status == "released" and link.exclusive_scope is None


async def test_deadline_after_reservation_compensates_before_remote_io(
    session: AsyncSession,
) -> None:
    mailbox = Account(provider="mail", identifier="deadline@example.test", status="active")
    session.add(mailbox)
    await session.commit()
    resource = AccountResource(
        account=StoredAccount(
            id=str(mailbox.id),
            identifier=mailbox.identifier,
            provider="mail",
            remote_id=None,
            profile=AccountProfile(first_name="Mailbox"),
            status=AccountStatus.ACTIVE,
            revision=0,
        )
    )
    deadline = datetime(2026, 8, 15, 12, tzinfo=UTC)
    values = iter((deadline - timedelta(seconds=1), deadline))
    service = Service()
    registration_store = store(session)
    flow = account_registration(
        SignupCredentials,
        service=service,
        store=registration_store,
        context_factory=lambda: None,
        provider="deadline-site",
        deadline=deadline,
        clock=lambda: next(values),
    )
    from eazy_sdk_accounts import RegistrationDeadlineError

    with pytest.raises(RegistrationDeadlineError):
        await flow.create(draft(), using=(resource,))
    assert service.calls == 0
    failed = await registration_store.load_account("ada@example.test", "deadline-site")
    assert failed is not None and failed.status is AccountStatus.REGISTRATION_FAILED


async def test_session_encoding_failure_rolls_back_remote_created_transition(
    session: AsyncSession,
) -> None:
    registration: SqlRegistrationStore[
        SignupCredentials, AccountProfile, SignupDetails, UserSession
    ] = SqlRegistrationStore(
        session,
        credentials_model=SignupCredentials,
        profile_model=AccountProfile,
    )
    attempt = await registration.begin_registration(
        identifier="ada@example.test",
        provider=None,
        draft=draft(),
        resources=(),
        correlation_id="attempt-4",
    )
    value = UserSession(
        access_token=SecretStr("created-access"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    with pytest.raises(TypeError, match="no session_model"):
        await registration.record_remote_created(
            attempt,
            AccountCreated(remote_id="remote-ada", session=value),
        )
    account = await registration.load_account("ada@example.test", None)
    assert account is not None and account.status is AccountStatus.PROVISIONING
    assert account.remote_id is None
    assert not (await session.execute(select(Session))).scalars().all()
