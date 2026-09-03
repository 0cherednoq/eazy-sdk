"""Transactional SQLModel bridge for registration lifecycle v2."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

from eazy_sdk_accounts import (
    AccountCreated,
    AccountDraft,
    AccountResource,
    AccountStatus,
    PendingRegistration,
    RegistrationAttempt,
    RegistrationConflictError,
    RegistrationNotFoundError,
    RegistrationResourceConflictError,
    StoredAccount,
    VerificationAccepted,
    VerificationChallenge,
)
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.session import SessionTransactionOrigin
from sqlmodel import SQLModel, select

from eazy_sdk_sqlmodel.adapter import SqlLinkRepository, SqlResourceConflictError
from eazy_sdk_sqlmodel.codecs import PlainPydanticCodec, SqlValueCodec
from eazy_sdk_sqlmodel.factory import DEFAULT_MODELS, SqlStorageModels
from eazy_sdk_sqlmodel.models import (
    normalize_identifier,
    normalize_provider,
    public_provider,
    utcnow,
)


class SqlRegistrationTransactionError(RuntimeError):
    """Registration store requires ownership of its event-sized transaction boundary."""


class SqlRegistrationStore[
    TCredentials: BaseModel,
    TProfile: BaseModel,
    TDetails,
    TSession: BaseModel,
]:
    """Event-sized transactions over the five account-storage v2 tables."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        credentials_model: type[TCredentials],
        profile_model: type[TProfile],
        session_model: type[TSession] | None = None,
        credentials_codec: SqlValueCodec[TCredentials] | None = None,
        profile_codec: SqlValueCodec[TProfile] | None = None,
        session_codec: SqlValueCodec[TSession] | None = None,
        models: SqlStorageModels = DEFAULT_MODELS,
    ) -> None:
        self._session = session
        self._credentials = credentials_codec or PlainPydanticCodec(credentials_model)
        self._profile = profile_codec or PlainPydanticCodec(profile_model)
        self._session_codec = session_codec or (
            PlainPydanticCodec(session_model) if session_model is not None else None
        )
        self._models = models
        self._links = SqlLinkRepository(session, models.link)

    async def load_account(
        self,
        identifier: str,
        provider: str | None,
    ) -> StoredAccount[TProfile, TSession] | None:
        account = await self._account(identifier, provider)
        return await self._stored(account) if account is not None else None

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
        account = await self._account(identifier, provider)
        if account is None:
            return None
        return self._credentials.decode(cast(Mapping[str, object], account.credentials))

    async def begin_registration(
        self,
        *,
        identifier: str,
        provider: str | None,
        draft: AccountDraft[TCredentials, TProfile, TDetails],
        resources: tuple[AccountResource, ...],
        correlation_id: str,
    ) -> RegistrationAttempt[TCredentials, TProfile, TDetails, TSession]:
        encoded_credentials = self._credentials.encode(draft.credentials)
        encoded_profile = self._profile.encode(draft.profile)
        canonical_identifier = normalize_identifier(identifier)
        canonical_provider = normalize_provider(provider)
        transaction = self._transaction()
        async with transaction:
            row = await self._account(canonical_identifier, provider)
            if row is None:
                row = cast(Any, self._models.account)(
                    identifier=canonical_identifier,
                    provider=canonical_provider,
                    status=AccountStatus.PROVISIONING.value,
                    revision=0,
                    credentials=dict(encoded_credentials),
                    profile=dict(encoded_profile),
                )
                self._session.add(row)
                try:
                    await self._session.flush()
                except IntegrityError as exc:
                    raise RegistrationConflictError(
                        "an account with this identifier and provider already exists"
                    ) from exc
            else:
                if row.status != AccountStatus.REGISTRATION_FAILED.value:
                    raise RegistrationConflictError(
                        "an account with this identifier and provider already exists"
                    )
                row = await self._cas_account(
                    row,
                    {
                        "status": AccountStatus.PROVISIONING.value,
                        "credentials": dict(encoded_credentials),
                        "profile": dict(encoded_profile),
                        "remote_id": None,
                        "meta": {},
                    },
                )

            links: list[Any] = []
            for resource in resources:
                try:
                    link = await self._links.reserve(
                        owner_account_id=row.id,
                        resource_account_id=UUID(resource.account.id),
                        relation=resource.relation,
                        exclusive_scope=resource.exclusive_scope,
                        meta=resource.meta,
                    )
                except (SqlResourceConflictError, ValueError) as exc:
                    raise RegistrationResourceConflictError(
                        resource.account.id,
                        resource.exclusive_scope or "",
                    ) from exc
                links.append(link)
            for link in links:
                self._session.add(
                    cast(Any, self._models.event)(
                        account_id=row.id,
                        type="account.link.reserved",
                        correlation_id=correlation_id,
                        link_id=link.id,
                        data={"exclusive_scope": link.exclusive_scope},
                    )
                )
            self._session.add(
                cast(Any, self._models.event)(
                    account_id=row.id,
                    type="registration.started",
                    correlation_id=correlation_id,
                )
            )
            await self._session.flush()
            stored = await self._stored(row)
        return RegistrationAttempt(
            account=stored,
            draft=draft,
            correlation_id=correlation_id,
            resources=resources,
        )

    async def record_remote_created(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        result: AccountCreated[TSession],
    ) -> StoredAccount[TProfile, TSession]:
        transaction = self._transaction()
        async with transaction:
            row = await self._account_by_id(attempt.account.id)
            if row is None:
                raise RegistrationNotFoundError("provisioning account no longer exists")
            if row.status != AccountStatus.PROVISIONING.value:
                raise RegistrationConflictError("account is not provisioning")
            verification: Any | None = None
            session: Any | None = None
            row = await self._cas_account(
                row,
                {
                    "remote_id": result.remote_id,
                    "status": (
                        AccountStatus.PENDING_VERIFICATION.value
                        if result.verification is not None
                        else AccountStatus.ACTIVE.value
                    ),
                    "meta": dict(result.meta),
                },
                expected_revision=attempt.account.revision,
            )
            if result.verification is not None:
                verification = self._verification_row(row.id, result.verification)
                self._session.add(verification)
            if result.session is not None:
                session = await self._save_session(row.id, result.session, key="registration")
            await self._session.flush()
            self._session.add(
                cast(Any, self._models.event)(
                    account_id=row.id,
                    type="registration.remote_created",
                    correlation_id=attempt.correlation_id,
                    session_id=session.id if session is not None else None,
                    verification_id=verification.id if verification is not None else None,
                    data={"remote_id": result.remote_id},
                )
            )
            if verification is not None:
                self._session.add(
                    cast(Any, self._models.event)(
                        account_id=row.id,
                        type="verification.requested",
                        correlation_id=attempt.correlation_id,
                        verification_id=verification.id,
                    )
                )
            if session is not None:
                self._session.add(
                    cast(Any, self._models.event)(
                        account_id=row.id,
                        type="session.created",
                        correlation_id=attempt.correlation_id,
                        session_id=session.id,
                    )
                )
            if verification is None:
                self._session.add(
                    cast(Any, self._models.event)(
                        account_id=row.id,
                        type="account.activated",
                        correlation_id=attempt.correlation_id,
                    )
                )
            await self._session.flush()
            stored = await self._stored(row)
        return stored

    async def record_registration_failed(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        *,
        reason: str,
    ) -> StoredAccount[TProfile, TSession]:
        transaction = self._transaction()
        async with transaction:
            row = await self._account_by_id(attempt.account.id)
            if row is None:
                raise RegistrationNotFoundError("provisioning account no longer exists")
            row = await self._cas_account(
                row,
                {
                    "status": AccountStatus.REGISTRATION_FAILED.value,
                    "meta": {**row.meta, "failure": reason},
                },
                expected_revision=attempt.account.revision,
            )
            links = await self._links.list_for_owner(row.id, status="active")
            for link in links:
                link_row = cast(Any, link)
                if not any(
                    link_row.resource_account_id == UUID(resource.account.id)
                    and link_row.relation == resource.relation
                    for resource in attempt.resources
                ):
                    continue
                scope = link_row.exclusive_scope
                await self._links.release(link)
                self._session.add(
                    cast(Any, self._models.event)(
                        account_id=row.id,
                        type="account.link.released",
                        correlation_id=attempt.correlation_id,
                        link_id=link_row.id,
                        data={"exclusive_scope": scope},
                    )
                )
            self._session.add(
                cast(Any, self._models.event)(
                    account_id=row.id,
                    type="registration.failed",
                    correlation_id=attempt.correlation_id,
                    data={"reason": reason},
                )
            )
            await self._session.flush()
            stored = await self._stored(row)
        return stored

    async def record_registration_uncertain(
        self,
        attempt: RegistrationAttempt[TCredentials, TProfile, TDetails, TSession],
        *,
        reason: str,
    ) -> StoredAccount[TProfile, TSession]:
        transaction = self._transaction()
        async with transaction:
            row = await self._account_by_id(attempt.account.id)
            if row is None:
                raise RegistrationNotFoundError("provisioning account no longer exists")
            row = await self._cas_account(
                row,
                {
                    "status": AccountStatus.RECONCILIATION_REQUIRED.value,
                    "meta": {**row.meta, "uncertain": reason},
                },
                expected_revision=attempt.account.revision,
            )
            self._session.add(
                cast(Any, self._models.event)(
                    account_id=row.id,
                    type="registration.outcome_unknown",
                    correlation_id=attempt.correlation_id,
                    data={"reason": reason},
                )
            )
            await self._session.flush()
            stored = await self._stored(row)
        return stored

    async def commit_verification(
        self,
        account: StoredAccount[TProfile, TSession],
        challenge: VerificationChallenge,
        result: VerificationAccepted[TSession],
    ) -> StoredAccount[TProfile, TSession]:
        transaction = self._transaction()
        async with transaction:
            row = await self._account_by_id(account.id)
            if row is None:
                raise RegistrationNotFoundError("account no longer exists")
            current = await self._pending_verification(row.id)
            if current is None or self._challenge(current) != challenge:
                raise RegistrationConflictError("verification challenge changed")
            current.status = "accepted"
            current.verified_at = utcnow()
            current.updated_at = utcnow()
            replacement: Any | None = None
            if result.next_verification is not None:
                replacement = self._verification_row(
                    row.id,
                    result.next_verification,
                    replaces_id=current.id,
                )
                self._session.add(replacement)
            row = await self._cas_account(
                row,
                {
                    "status": (
                        AccountStatus.PENDING_VERIFICATION.value
                        if replacement is not None
                        else AccountStatus.ACTIVE.value
                    ),
                    "meta": {**row.meta, **dict(result.meta)},
                },
                expected_revision=account.revision,
            )
            session = None
            if result.session is not None:
                session = await self._save_session(row.id, result.session, key="registration")
            await self._session.flush()
            self._session.add(
                cast(Any, self._models.event)(
                    account_id=row.id,
                    type="verification.accepted",
                    verification_id=current.id,
                    session_id=session.id if session is not None else None,
                )
            )
            if replacement is not None:
                self._session.add(
                    cast(Any, self._models.event)(
                        account_id=row.id,
                        type="verification.requested",
                        verification_id=replacement.id,
                    )
                )
            else:
                self._session.add(
                    cast(Any, self._models.event)(
                        account_id=row.id,
                        type="account.activated",
                        session_id=session.id if session is not None else None,
                    )
                )
            if session is not None:
                self._session.add(
                    cast(Any, self._models.event)(
                        account_id=row.id,
                        type="session.created",
                        session_id=session.id,
                    )
                )
            await self._session.flush()
            stored = await self._stored(row)
        return stored

    async def commit_resend(
        self,
        account: StoredAccount[TProfile, TSession],
        previous: VerificationChallenge,
        replacement: VerificationChallenge,
    ) -> StoredAccount[TProfile, TSession]:
        transaction = self._transaction()
        async with transaction:
            row = await self._account_by_id(account.id)
            if row is None:
                raise RegistrationNotFoundError("account no longer exists")
            current = await self._pending_verification(row.id)
            if current is None or self._challenge(current) != previous:
                raise RegistrationConflictError("verification challenge changed")
            current.status = "superseded"
            current.updated_at = utcnow()
            replacement_row = cast(
                Any,
                self._verification_row(
                    row.id,
                    replacement,
                    replaces_id=current.id,
                ),
            )
            self._session.add(replacement_row)
            row = await self._cas_account(row, {}, expected_revision=account.revision)
            await self._session.flush()
            self._session.add(
                cast(Any, self._models.event)(
                    account_id=row.id,
                    type="verification.resent",
                    verification_id=replacement_row.id,
                    data={"replaces_id": str(current.id)},
                )
            )
            await self._session.flush()
            stored = await self._stored(row)
        return stored

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        root = self._session.get_transaction()
        sync_root = root.sync_transaction if root is not None else None
        if root is not None and (
            sync_root is None or sync_root.origin is not SessionTransactionOrigin.AUTOBEGIN
        ):
            raise SqlRegistrationTransactionError(
                "SqlRegistrationStore cannot run inside an explicit outer transaction"
            )
        try:
            if root is None:
                async with self._session.begin():
                    yield
            else:
                async with self._session.begin_nested():
                    yield
                await self._session.commit()
        except BaseException:
            if self._session.in_transaction():
                await self._session.rollback()
            raise

    async def _account(self, identifier: str, provider: str | None) -> Any | None:
        model = cast(Any, self._models.account)
        statement = select(model).where(
            model.identifier == normalize_identifier(identifier),
            model.provider == normalize_provider(provider),
        )
        return (await self._session.execute(statement)).scalars().first()

    async def _account_by_id(self, value: str) -> Any | None:
        return await self._session.get(self._models.account, UUID(value))

    async def _cas_account(
        self,
        row: Any,
        values: Mapping[str, object],
        *,
        expected_revision: int | None = None,
    ) -> Any:
        table = cast(Any, self._models.account).__table__
        expected = row.revision if expected_revision is None else expected_revision
        columns = dict(values)
        columns["revision"] = table.c.revision + 1
        columns["updated_at"] = utcnow()
        result = cast(
            CursorResult[tuple[object, ...]],
            await self._session.execute(
                update(table)
                .where(table.c.id == row.id, table.c.revision == expected)
                .values(**columns)
            ),
        )
        if result.rowcount != 1:
            raise RegistrationConflictError("account revision changed")
        await self._session.refresh(row)
        return row

    async def _stored(self, account: Any) -> StoredAccount[TProfile, TSession]:
        verification = await self._pending_verification(account.id)
        session = await self._active_session(account.id)
        return StoredAccount(
            id=str(account.id),
            identifier=account.identifier,
            provider=public_provider(account.provider),
            remote_id=account.remote_id,
            profile=self._profile.decode(cast(Mapping[str, object], account.profile)),
            status=AccountStatus(account.status),
            revision=account.revision,
            challenge=self._challenge(verification) if verification is not None else None,
            session=session,
            meta=cast(Mapping[str, object], account.meta),
        )

    async def _pending_verification(self, account_id: UUID) -> Any | None:
        model = cast(Any, self._models.verification)
        statement = (
            select(model)
            .where(model.account_id == account_id, model.status == "pending")
            .order_by(model.created_at.desc())
        )
        return (await self._session.execute(statement)).scalars().first()

    async def _active_session(self, account_id: UUID) -> TSession | None:
        if self._session_codec is None:
            return None
        model = cast(Any, self._models.session)
        statement = (
            select(model)
            .where(
                model.account_id == account_id,
                model.is_active.is_(True),
                model.key == "registration",
            )
            .order_by(model.created_at.desc())
        )
        row = (await self._session.execute(statement)).scalars().first()
        if row is None:
            return None
        return self._session_codec.decode(cast(Mapping[str, object], row.payload))

    async def _save_session(self, account_id: UUID, value: TSession, *, key: str) -> Any:
        if self._session_codec is None:
            raise TypeError("registration returned a session, but no session_model is configured")
        payload = self._session_codec.encode(value)
        model = cast(Any, self._models.session)
        statement = select(model).where(model.account_id == account_id, model.key == key)
        existing = (await self._session.execute(statement)).scalars().first()
        if existing is None:
            row = model(
                account_id=account_id,
                key=key,
                revision=1,
                kind="registration",
                payload=dict(payload),
                expires_at=self._session_expiry(value),
                is_active=True,
            )
            self._session.add(row)
            await self._session.flush()
            return row
        table = cast(Any, self._models.session).__table__
        result = cast(
            CursorResult[tuple[object, ...]],
            await self._session.execute(
                update(table)
                .where(table.c.id == existing.id, table.c.revision == existing.revision)
                .values(
                    payload=dict(payload),
                    revision=table.c.revision + 1,
                    expires_at=self._session_expiry(value),
                    is_active=True,
                    updated_at=utcnow(),
                )
            ),
        )
        if result.rowcount != 1:
            raise RegistrationConflictError("session revision changed")
        await self._session.refresh(existing)
        return existing

    @staticmethod
    def _session_expiry(value: object) -> object:
        expires_at = getattr(value, "expires_at", None)
        return expires_at

    def _verification_row(
        self,
        account_id: UUID,
        challenge: VerificationChallenge,
        *,
        replaces_id: UUID | None = None,
    ) -> SQLModel:
        via_account_id = (
            UUID(challenge.via_account_id) if challenge.via_account_id is not None else None
        )
        return self._models.verification(
            account_id=account_id,
            via_account_id=via_account_id,
            challenge_id=challenge.id,
            kind=challenge.kind,
            status="pending",
            target=challenge.target,
            expires_at=challenge.expires_at,
            attempts_remaining=challenge.attempts_remaining,
            replaces_id=replaces_id,
            meta=dict(challenge.meta),
        )

    @staticmethod
    def _challenge(row: Any) -> VerificationChallenge:
        return VerificationChallenge(
            id=row.challenge_id,
            kind=row.kind,
            target=row.target,
            expires_at=row.expires_at,
            attempts_remaining=row.attempts_remaining,
            via_account_id=str(row.via_account_id) if row.via_account_id is not None else None,
            meta=cast(Mapping[str, object], row.meta),
        )


__all__ = ["SqlRegistrationStore", "SqlRegistrationTransactionError"]
