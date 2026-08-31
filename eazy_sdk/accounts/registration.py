"""Transport-neutral account creation and verification lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, Self
from uuid import uuid4

from eazy_sdk.models import default_model_adapters

from .session import SessionAdopter


@dataclass(frozen=True, slots=True)
class AccountIdentifier:
    """Mark the one non-secret credentials field used as account identity."""


@dataclass(frozen=True, slots=True, repr=False)
class AccountDraft[TCredentials, TProfile, TDetails]:
    """Domain registration input; it is never used as a wire request automatically."""

    credentials: TCredentials
    profile: TProfile
    details: TDetails

    def __repr__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True)
class VerificationChallenge:
    id: str
    kind: str
    target: str | None = None
    expires_at: datetime | None = None
    attempts_remaining: int | None = None
    via_account_id: str | None = None
    meta: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.id or not self.kind:
            raise ValueError("verification challenge id and kind cannot be empty")
        if self.attempts_remaining is not None and self.attempts_remaining < 0:
            raise ValueError("verification attempts_remaining cannot be negative")


@dataclass(frozen=True, slots=True)
class AccountCreated[TSession]:
    """The remote account exists after the service returns this value."""

    remote_id: str | None = None
    verification: VerificationChallenge | None = None
    session: TSession | None = field(default=None, repr=False)
    meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class VerificationAccepted[TSession]:
    next_verification: VerificationChallenge | None = None
    session: TSession | None = field(default=None, repr=False)
    meta: Mapping[str, object] = field(default_factory=dict, repr=False)


class AccountStatus(StrEnum):
    PROVISIONING = "provisioning"
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    REGISTRATION_FAILED = "registration_failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    INACTIVE = "inactive"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class StoredAccount[TProfile, TSession]:
    id: str
    identifier: str
    provider: str | None
    remote_id: str | None
    profile: TProfile
    status: AccountStatus
    revision: int
    challenge: VerificationChallenge | None = None
    session: TSession | None = field(default=None, repr=False)
    meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class AccountResource:
    """An existing managed account used by a registration attempt."""

    account: StoredAccount[Any, Any]
    relation: str = "registration_identity"
    exclusive_scope: str | None = "registration"
    meta: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.relation.strip():
            raise ValueError("account resource relation cannot be empty")
        if self.exclusive_scope is not None and not self.exclusive_scope.strip():
            raise ValueError("account resource exclusive_scope cannot be empty")


@dataclass(frozen=True, slots=True)
class RegistrationAttempt[TCredentials, TProfile, TDetails, TSession]:
    """A durable provisioning aggregate created before the remote effect."""

    account: StoredAccount[TProfile, TSession]
    draft: AccountDraft[TCredentials, TProfile, TDetails] = field(repr=False)
    correlation_id: str
    resources: tuple[AccountResource, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteRegistration[TAccount, TSession]:
    account: TAccount
    session: TSession | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class PendingRegistration[TAccount]:
    account: TAccount
    challenge: VerificationChallenge


type RegistrationOutcome[TAccount, TSession] = (
    CompleteRegistration[TAccount, TSession] | PendingRegistration[TAccount]
)


class RegistrationError(RuntimeError):
    pass


class RegistrationConfigurationError(RegistrationError):
    pass


class RegistrationNotFoundError(RegistrationError):
    pass


class RegistrationConflictError(RegistrationError):
    pass


class RegistrationResourceConflictError(RegistrationConflictError):
    def __init__(self, resource_account_id: str, exclusive_scope: str) -> None:
        self.resource_account_id = resource_account_id
        self.exclusive_scope = exclusive_scope
        super().__init__(
            f"account resource {resource_account_id} is already reserved in "
            f"scope {exclusive_scope!r}"
        )


class RegistrationOutcomeUnknownError(RegistrationError):
    """A service knows that a remote effect may have happened, but cannot prove the outcome."""


class RegistrationReconciliationRequiredError(RegistrationError):
    def __init__(self, account: StoredAccount[Any, Any]) -> None:
        self.account = account
        super().__init__(
            f"registration outcome for account {account.id} is unknown; reconcile before retry"
        )


class VerificationLimitError(RegistrationError):
    pass


class VerificationCycleError(RegistrationError):
    pass


class RegistrationCancelledError(RegistrationError):
    pass


class RegistrationDeadlineError(RegistrationError):
    pass


class RegistrationPersistenceError(RegistrationError):
    """Remote state changed, but the corresponding local transaction failed."""

    def __init__(
        self,
        remote_id: str | None,
        *,
        event: str = "account.created",
    ) -> None:
        self.remote_id = remote_id
        self.event = event
        super().__init__(f"remote {event} succeeded, but the local lifecycle commit failed")


class CredentialsCodec[TCredentials](Protocol):
    """Application-owned encryption/serialization boundary for credentials."""

    def encode(self, value: TCredentials) -> object: ...

    def decode(self, value: object) -> TCredentials: ...


class AccountRegistrationService[
    TCredentials,
    TProfile,
    TDetails,
    TSession,
    TContext,
](Protocol):
    async def create(
        self,
        draft: AccountDraft[TCredentials, TProfile, TDetails],
        context: TContext,
    ) -> AccountCreated[TSession]: ...

    async def verify(
        self,
        account: StoredAccount[TProfile, TSession],
        challenge: VerificationChallenge,
        proof: object,
        context: TContext,
    ) -> VerificationAccepted[TSession]: ...


class VerificationProvider[TContext](Protocol):
    async def resolve(
        self,
        challenge: VerificationChallenge,
        context: TContext,
    ) -> object: ...


class RegistrationStore[TCredentials, TProfile, TDetails, TSession](Protocol):
    async def load_account(
        self,
        identifier: str,
        provider: str | None,
    ) -> StoredAccount[TProfile, TSession] | None: ...

    async def load_pending(
        self,
        identifier: str,
        provider: str | None,
    ) -> PendingRegistration[StoredAccount[TProfile, TSession]] | None: ...

    async def begin_registration(
        self,
        *,
        identifier: str,
        provider: str | None,
        draft: AccountDraft[TCredentials, TProfile, TDetails],
        resources: tuple[AccountResource, ...],
        correlation_id: str,
    ) -> RegistrationAttempt[TCredentials, TProfile, TDetails, TSession]: ...

    async def record_remote_created(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        result: AccountCreated[TSession],
    ) -> StoredAccount[TProfile, TSession]: ...

    async def record_registration_failed(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        *,
        reason: str,
    ) -> StoredAccount[TProfile, TSession]: ...

    async def record_registration_uncertain(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        *,
        reason: str,
    ) -> StoredAccount[TProfile, TSession]: ...

    async def commit_verification(
        self,
        account: StoredAccount[TProfile, TSession],
        challenge: VerificationChallenge,
        result: VerificationAccepted[TSession],
    ) -> StoredAccount[TProfile, TSession]: ...

    async def commit_resend(
        self,
        account: StoredAccount[TProfile, TSession],
        previous: VerificationChallenge,
        replacement: VerificationChallenge,
    ) -> StoredAccount[TProfile, TSession]: ...


@dataclass(slots=True, repr=False)
class _MemoryAccount[TCredentials, TProfile, TSession]:
    account: StoredAccount[TProfile, TSession]
    encoded_credentials: object = field(repr=False)


class MemoryRegistrationStore[TCredentials, TProfile, TDetails, TSession]:
    """Transactional in-memory bridge with mandatory explicit credentials codec."""

    def __init__(self, credentials: CredentialsCodec[TCredentials]) -> None:
        self._credentials = credentials
        self._accounts: dict[tuple[str, str], _MemoryAccount[TCredentials, TProfile, TSession]] = {}
        self._reservations: dict[tuple[str, str], str] = {}
        self._attempt_resources: dict[str, tuple[AccountResource, ...]] = {}
        self._lock = asyncio.Lock()
        self.commits: list[str] = []

    def _key(self, identifier: str, provider: str | None) -> tuple[str, str]:
        return (provider or "", identifier)

    async def load_account(
        self,
        identifier: str,
        provider: str | None,
    ) -> StoredAccount[TProfile, TSession] | None:
        value = self._accounts.get(self._key(identifier, provider))
        return deepcopy(value.account) if value is not None else None

    async def load_pending(
        self,
        identifier: str,
        provider: str | None,
    ) -> PendingRegistration[StoredAccount[TProfile, TSession]] | None:
        account = await self.load_account(identifier, provider)
        if account is None or account.challenge is None:
            return None
        return PendingRegistration(account, account.challenge)

    async def load_credentials(
        self,
        identifier: str,
        provider: str | None,
    ) -> TCredentials | None:
        value = self._accounts.get(self._key(identifier, provider))
        if value is None:
            return None
        return self._credentials.decode(deepcopy(value.encoded_credentials))

    async def begin_registration(
        self,
        *,
        identifier: str,
        provider: str | None,
        draft: AccountDraft[TCredentials, TProfile, TDetails],
        resources: tuple[AccountResource, ...],
        correlation_id: str,
    ) -> RegistrationAttempt[TCredentials, TProfile, TDetails, TSession]:
        key = self._key(identifier, provider)
        encoded = deepcopy(self._credentials.encode(draft.credentials))
        async with self._lock:
            existing = self._accounts.get(key)
            if (
                existing is not None
                and existing.account.status is not AccountStatus.REGISTRATION_FAILED
            ):
                raise RegistrationConflictError(
                    "an account with this identifier and provider already exists"
                )
            account_id = (
                existing.account.id
                if existing is not None
                else f"account-{len(self._accounts) + 1}"
            )
            for resource in resources:
                if resource.account.id == account_id:
                    raise RegistrationConflictError("an account cannot reserve itself")
                if resource.exclusive_scope is None:
                    continue
                reservation_key = (resource.account.id, resource.exclusive_scope)
                owner = self._reservations.get(reservation_key)
                if owner is not None and owner != account_id:
                    raise RegistrationResourceConflictError(*reservation_key)
            account: StoredAccount[TProfile, TSession] = StoredAccount(
                id=account_id,
                identifier=identifier,
                provider=provider,
                remote_id=None,
                profile=deepcopy(draft.profile),
                status=AccountStatus.PROVISIONING,
                revision=(existing.account.revision + 1 if existing is not None else 0),
            )
            self._accounts[key] = _MemoryAccount(account, encoded)
            for resource in resources:
                if resource.exclusive_scope is not None:
                    self._reservations[(resource.account.id, resource.exclusive_scope)] = account_id
                self.commits.append("account.link.reserved")
            self._attempt_resources[account_id] = resources
            self.commits.append("registration.started")
            return RegistrationAttempt(
                account=deepcopy(account),
                draft=deepcopy(draft),
                correlation_id=correlation_id,
                resources=deepcopy(resources),
            )

    async def record_remote_created(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        result: AccountCreated[TSession],
    ) -> StoredAccount[TProfile, TSession]:
        key = self._key(attempt.account.identifier, attempt.account.provider)
        async with self._lock:
            current = self._accounts.get(key)
            if current is None:
                raise RegistrationNotFoundError("provisioning account no longer exists")
            if current.account.revision != attempt.account.revision:
                raise RegistrationConflictError("account revision changed during registration")
            updated = replace(
                current.account,
                remote_id=result.remote_id,
                status=(
                    AccountStatus.PENDING_VERIFICATION
                    if result.verification is not None
                    else AccountStatus.ACTIVE
                ),
                revision=current.account.revision + 1,
                challenge=result.verification,
                session=deepcopy(result.session),
                meta=dict(result.meta),
            )
            self._accounts[key] = _MemoryAccount(updated, current.encoded_credentials)
            self.commits.append("registration.remote_created")
            if result.verification is not None:
                self.commits.append("verification.requested")
            if result.session is not None:
                self.commits.append("session.created")
            if result.verification is None:
                self.commits.append("account.activated")
            return deepcopy(updated)

    async def record_registration_failed(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        *,
        reason: str,
    ) -> StoredAccount[TProfile, TSession]:
        key = self._key(attempt.account.identifier, attempt.account.provider)
        async with self._lock:
            current = self._accounts.get(key)
            if current is None:
                raise RegistrationNotFoundError("provisioning account no longer exists")
            if current.account.revision != attempt.account.revision:
                raise RegistrationConflictError("account revision changed during registration")
            updated = replace(
                current.account,
                status=AccountStatus.REGISTRATION_FAILED,
                revision=current.account.revision + 1,
                meta={**current.account.meta, "failure": reason},
            )
            self._accounts[key] = _MemoryAccount(updated, current.encoded_credentials)
            for resource in self._attempt_resources.pop(updated.id, ()):
                if resource.exclusive_scope is not None:
                    self._reservations.pop((resource.account.id, resource.exclusive_scope), None)
                self.commits.append("account.link.released")
            self.commits.append("registration.failed")
            return deepcopy(updated)

    async def record_registration_uncertain(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        *,
        reason: str,
    ) -> StoredAccount[TProfile, TSession]:
        key = self._key(attempt.account.identifier, attempt.account.provider)
        async with self._lock:
            current = self._accounts.get(key)
            if current is None:
                raise RegistrationNotFoundError("provisioning account no longer exists")
            if current.account.revision != attempt.account.revision:
                raise RegistrationConflictError("account revision changed during registration")
            updated = replace(
                current.account,
                status=AccountStatus.RECONCILIATION_REQUIRED,
                revision=current.account.revision + 1,
                meta={**current.account.meta, "uncertain": reason},
            )
            self._accounts[key] = _MemoryAccount(updated, current.encoded_credentials)
            self.commits.append("registration.outcome_unknown")
            return deepcopy(updated)

    async def commit_verification(
        self,
        account: StoredAccount[TProfile, TSession],
        challenge: VerificationChallenge,
        result: VerificationAccepted[TSession],
    ) -> StoredAccount[TProfile, TSession]:
        key = self._key(account.identifier, account.provider)
        async with self._lock:
            current = self._accounts.get(key)
            if current is None:
                raise RegistrationNotFoundError("account no longer exists")
            if current.account.revision != account.revision:
                raise RegistrationConflictError("account revision changed during verification")
            if current.account.challenge != challenge:
                raise RegistrationConflictError("verification challenge changed")
            updated = replace(
                current.account,
                status=(
                    AccountStatus.PENDING_VERIFICATION
                    if result.next_verification is not None
                    else AccountStatus.ACTIVE
                ),
                revision=current.account.revision + 1,
                challenge=result.next_verification,
                session=(result.session if result.session is not None else current.account.session),
                meta={**current.account.meta, **result.meta},
            )
            self._accounts[key] = _MemoryAccount(updated, current.encoded_credentials)
            self.commits.append(f"verification.{challenge.kind}.accepted")
            return deepcopy(updated)

    async def commit_resend(
        self,
        account: StoredAccount[TProfile, TSession],
        previous: VerificationChallenge,
        replacement: VerificationChallenge,
    ) -> StoredAccount[TProfile, TSession]:
        key = self._key(account.identifier, account.provider)
        async with self._lock:
            current = self._accounts.get(key)
            if current is None:
                raise RegistrationNotFoundError("account no longer exists")
            if current.account.revision != account.revision:
                raise RegistrationConflictError("account revision changed during resend")
            if current.account.challenge != previous:
                raise RegistrationConflictError("verification challenge changed")
            updated = replace(
                current.account,
                revision=current.account.revision + 1,
                challenge=replacement,
            )
            self._accounts[key] = _MemoryAccount(updated, current.encoded_credentials)
            self.commits.append(f"verification.{previous.kind}.resent")
            return deepcopy(updated)


@dataclass(frozen=True, slots=True)
class _CredentialsSchema[TCredentials]:
    model: type[TCredentials]
    identifier_field: str

    @classmethod
    def compile(cls, model: type[TCredentials]) -> _CredentialsSchema[TCredentials]:
        fields = default_model_adapters().fields(model)
        marked = [
            field.name
            for field in fields
            if any(isinstance(marker, AccountIdentifier) for marker in field.metadata)
        ]
        if len(marked) != 1:
            raise RegistrationConfigurationError(
                "credentials model must declare exactly one AccountIdentifier field"
            )
        return cls(model, marked[0])

    def identifier(self, credentials: TCredentials) -> str:
        parsed = default_model_adapters().load(self.model, credentials)
        value = getattr(parsed, self.identifier_field, None)
        if not isinstance(value, str) or not value.strip():
            raise RegistrationConfigurationError(
                "AccountIdentifier field must contain a non-empty string"
            )
        return value.strip()


class RegistrationFlow[
    TCredentials,
    TProfile,
    TDetails,
    TSession,
    TContext,
]:
    """Create, persist, verify, resume, and hand off sessions without transport knowledge."""

    def __init__(
        self,
        credentials_model: type[TCredentials],
        *,
        service: AccountRegistrationService[
            TCredentials,
            TProfile,
            TDetails,
            TSession,
            TContext,
        ],
        store: RegistrationStore[TCredentials, TProfile, TDetails, TSession],
        context_factory: Callable[[], TContext],
        provider: str | None = None,
        verification: Mapping[str, VerificationProvider[TContext]] | None = None,
        session_lifecycle: SessionAdopter[TSession] | None = None,
        max_verification_steps: int = 3,
        deadline: datetime | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        if max_verification_steps < 1:
            raise RegistrationConfigurationError("max_verification_steps must be at least one")
        if deadline is not None and deadline.tzinfo is None:
            raise RegistrationConfigurationError("registration deadline must be timezone-aware")
        self._credentials = _CredentialsSchema.compile(credentials_model)
        self._service = service
        self._store = store
        self._context_factory = context_factory
        self._provider = provider
        self._verification = dict(verification or {})
        self._session_lifecycle = session_lifecycle
        self._max_verification_steps = max_verification_steps
        self._deadline = deadline
        self._clock = clock
        self._cancelled = cancelled
        self._create_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def create(
        self,
        draft: AccountDraft[TCredentials, TProfile, TDetails],
        *,
        using: tuple[AccountResource, ...] = (),
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        identifier = self._credentials.identifier(draft.credentials)
        lock = await self._create_lock(identifier)
        async with lock:
            self._check_control()
            existing = await self._store.load_account(identifier, self._provider)
            if existing is not None and existing.status is not AccountStatus.REGISTRATION_FAILED:
                if existing.status in {
                    AccountStatus.PROVISIONING,
                    AccountStatus.RECONCILIATION_REQUIRED,
                }:
                    if existing.status is AccountStatus.RECONCILIATION_REQUIRED:
                        raise RegistrationReconciliationRequiredError(existing)
                    raise RegistrationConflictError("registration is already provisioning")
                return await self._outcome_from_account(existing, continue_automatic=True)

            correlation_id = str(uuid4())
            attempt = await self._store.begin_registration(
                identifier=identifier,
                provider=self._provider,
                draft=draft,
                resources=using,
                correlation_id=correlation_id,
            )
            try:
                self._check_control()
                created = await self._service.create(draft, self._context_factory())
            except RegistrationOutcomeUnknownError as exc:
                try:
                    uncertain = await self._store.record_registration_uncertain(
                        attempt,
                        reason=type(exc).__name__,
                    )
                except Exception as persistence_error:
                    raise RegistrationPersistenceError(
                        None, event="registration.outcome_unknown"
                    ) from persistence_error
                raise RegistrationReconciliationRequiredError(uncertain) from exc
            except BaseException as exc:
                try:
                    await self._store.record_registration_failed(
                        attempt,
                        reason=type(exc).__name__,
                    )
                except Exception as persistence_error:
                    raise RegistrationPersistenceError(
                        None, event="registration.failed"
                    ) from persistence_error
                raise
            try:
                account = await self._store.record_remote_created(
                    attempt,
                    created,
                )
            except Exception as exc:
                raise RegistrationPersistenceError(created.remote_id) from exc

            if account.challenge is None:
                return await self._complete(account)
            return await self._continue_automatically(
                PendingRegistration(account, account.challenge),
                completed_steps=0,
                seen=frozenset(),
            )

    async def verify(
        self,
        pending: PendingRegistration[StoredAccount[TProfile, TSession]],
        proof: object,
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        outcome = await self._verify_once(pending, proof)
        if isinstance(outcome, CompleteRegistration):
            return outcome
        return await self._continue_automatically(
            outcome,
            completed_steps=1,
            seen=frozenset({self._challenge_key(pending.challenge)}),
        )

    async def resume(
        self,
        identifier: str,
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        self._check_control()
        account = await self._store.load_account(identifier.strip(), self._provider)
        if account is None:
            raise RegistrationNotFoundError("registration account was not found")
        if account.status is AccountStatus.RECONCILIATION_REQUIRED:
            raise RegistrationReconciliationRequiredError(account)
        return await self._outcome_from_account(account, continue_automatic=True)

    async def resend(
        self,
        pending: PendingRegistration[StoredAccount[TProfile, TSession]],
    ) -> PendingRegistration[StoredAccount[TProfile, TSession]]:
        self._check_control()
        resend = getattr(self._service, "resend", None)
        if not callable(resend):
            raise RegistrationConfigurationError("registration service does not support resend")
        replacement = await resend(
            pending.account,
            pending.challenge,
            self._context_factory(),
        )
        if not isinstance(replacement, VerificationChallenge):
            raise RegistrationConfigurationError(
                "registration resend must return VerificationChallenge"
            )
        try:
            account = await self._store.commit_resend(
                pending.account,
                pending.challenge,
                replacement,
            )
        except Exception as exc:
            raise RegistrationPersistenceError(
                pending.account.remote_id,
                event="verification.resent",
            ) from exc
        return PendingRegistration(account, replacement)

    async def _verify_once(
        self,
        pending: PendingRegistration[StoredAccount[TProfile, TSession]],
        proof: object,
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        self._check_control()
        accepted = await self._service.verify(
            pending.account,
            pending.challenge,
            proof,
            self._context_factory(),
        )
        try:
            account = await self._store.commit_verification(
                pending.account,
                pending.challenge,
                accepted,
            )
        except Exception as exc:
            raise RegistrationPersistenceError(
                pending.account.remote_id,
                event="verification.accepted",
            ) from exc
        if account.challenge is not None:
            return PendingRegistration(account, account.challenge)
        return await self._complete(account)

    async def _continue_automatically(
        self,
        pending: PendingRegistration[StoredAccount[TProfile, TSession]],
        *,
        completed_steps: int,
        seen: frozenset[tuple[str, str]],
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        current = pending
        steps = completed_steps
        visited = set(seen)
        while True:
            self._check_control()
            provider = self._verification.get(current.challenge.kind)
            if provider is None:
                return current
            key = self._challenge_key(current.challenge)
            if key in visited:
                raise VerificationCycleError("verification challenge cycle detected")
            if steps >= self._max_verification_steps:
                raise VerificationLimitError("registration exceeded the verification step budget")
            visited.add(key)
            proof = await provider.resolve(current.challenge, self._context_factory())
            outcome = await self._verify_once(current, proof)
            steps += 1
            if isinstance(outcome, CompleteRegistration):
                return outcome
            current = outcome

    async def _outcome_from_account(
        self,
        account: StoredAccount[TProfile, TSession],
        *,
        continue_automatic: bool,
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        if account.challenge is None:
            return await self._complete(account)
        pending = PendingRegistration(account, account.challenge)
        if not continue_automatic:
            return pending
        return await self._continue_automatically(
            pending,
            completed_steps=0,
            seen=frozenset(),
        )

    async def _complete(
        self,
        account: StoredAccount[TProfile, TSession],
    ) -> CompleteRegistration[StoredAccount[TProfile, TSession], TSession]:
        session = account.session
        if session is not None and self._session_lifecycle is not None:
            try:
                await self._session_lifecycle.adopt(session)
            except Exception as exc:
                raise RegistrationPersistenceError(
                    account.remote_id,
                    event="session.adopted",
                ) from exc
        return CompleteRegistration(account, session)

    async def _create_lock(self, identifier: str) -> asyncio.Lock:
        key = (self._provider or "", identifier)
        async with self._locks_guard:
            return self._create_locks.setdefault(key, asyncio.Lock())

    def _check_control(self) -> None:
        if self._cancelled():
            raise RegistrationCancelledError("registration was cancelled")
        if self._deadline is not None and self._clock() >= self._deadline:
            raise RegistrationDeadlineError("registration deadline was exceeded")

    @staticmethod
    def _challenge_key(challenge: VerificationChallenge) -> tuple[str, str]:
        return (challenge.kind, challenge.id)


class SyncRegistrationFlow[TCredentials, TProfile, TDetails, TSession, TContext]:
    """Synchronous runner over the exact same asynchronous lifecycle state machine."""

    def __init__(
        self,
        flow: RegistrationFlow[TCredentials, TProfile, TDetails, TSession, TContext],
    ) -> None:
        self._flow = flow
        self._runner = asyncio.Runner()
        self._closed = False

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("registration runner is closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._runner.close()
            self._closed = True

    def _run[T](self, awaitable: Coroutine[Any, Any, T]) -> T:
        if self._closed:
            raise RuntimeError("registration runner is closed")
        return self._runner.run(awaitable)

    def create(
        self,
        draft: AccountDraft[TCredentials, TProfile, TDetails],
        *,
        using: tuple[AccountResource, ...] = (),
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        return self._run(self._flow.create(draft, using=using))

    def verify(
        self,
        pending: PendingRegistration[StoredAccount[TProfile, TSession]],
        proof: object,
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        return self._run(self._flow.verify(pending, proof))

    def resume(
        self,
        identifier: str,
    ) -> RegistrationOutcome[StoredAccount[TProfile, TSession], TSession]:
        return self._run(self._flow.resume(identifier))

    def resend(
        self,
        pending: PendingRegistration[StoredAccount[TProfile, TSession]],
    ) -> PendingRegistration[StoredAccount[TProfile, TSession]]:
        return self._run(self._flow.resend(pending))


def account_registration[
    TCredentials,
    TProfile,
    TDetails,
    TSession,
    TContext,
](
    credentials_model: type[TCredentials],
    *,
    service: AccountRegistrationService[
        TCredentials,
        TProfile,
        TDetails,
        TSession,
        TContext,
    ],
    store: RegistrationStore[TCredentials, TProfile, TDetails, TSession],
    context_factory: Callable[[], TContext],
    provider: str | None = None,
    verification: Mapping[str, VerificationProvider[TContext]] | None = None,
    session_lifecycle: SessionAdopter[TSession] | None = None,
    max_verification_steps: int = 3,
    deadline: datetime | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    cancelled: Callable[[], bool] = lambda: False,
) -> RegistrationFlow[TCredentials, TProfile, TDetails, TSession, TContext]:
    return RegistrationFlow(
        credentials_model,
        service=service,
        store=store,
        context_factory=context_factory,
        provider=provider,
        verification=verification,
        session_lifecycle=session_lifecycle,
        max_verification_steps=max_verification_steps,
        deadline=deadline,
        clock=clock,
        cancelled=cancelled,
    )


def sync_account_registration[
    TCredentials,
    TProfile,
    TDetails,
    TSession,
    TContext,
](
    credentials_model: type[TCredentials],
    *,
    service: AccountRegistrationService[
        TCredentials,
        TProfile,
        TDetails,
        TSession,
        TContext,
    ],
    store: RegistrationStore[TCredentials, TProfile, TDetails, TSession],
    context_factory: Callable[[], TContext],
    provider: str | None = None,
    verification: Mapping[str, VerificationProvider[TContext]] | None = None,
    session_lifecycle: SessionAdopter[TSession] | None = None,
    max_verification_steps: int = 3,
    deadline: datetime | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    cancelled: Callable[[], bool] = lambda: False,
) -> SyncRegistrationFlow[TCredentials, TProfile, TDetails, TSession, TContext]:
    return SyncRegistrationFlow(
        account_registration(
            credentials_model,
            service=service,
            store=store,
            context_factory=context_factory,
            provider=provider,
            verification=verification,
            session_lifecycle=session_lifecycle,
            max_verification_steps=max_verification_steps,
            deadline=deadline,
            clock=clock,
            cancelled=cancelled,
        )
    )


__all__ = [
    "AccountCreated",
    "AccountDraft",
    "AccountIdentifier",
    "AccountRegistrationService",
    "AccountStatus",
    "CompleteRegistration",
    "CredentialsCodec",
    "MemoryRegistrationStore",
    "PendingRegistration",
    "RegistrationCancelledError",
    "RegistrationConfigurationError",
    "RegistrationConflictError",
    "RegistrationDeadlineError",
    "RegistrationError",
    "RegistrationFlow",
    "RegistrationNotFoundError",
    "RegistrationPersistenceError",
    "StoredAccount",
    "SyncRegistrationFlow",
    "VerificationAccepted",
    "VerificationChallenge",
    "VerificationCycleError",
    "VerificationLimitError",
    "VerificationProvider",
    "account_registration",
    "sync_account_registration",
]
