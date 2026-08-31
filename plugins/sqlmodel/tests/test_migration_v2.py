from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from eazy_sdk_sqlmodel import (
    DEFAULT_PROVIDER,
    MigrationAlreadyAppliedError,
    MigrationConflictError,
    inspect_v1,
    migrate_v1_to_v2,
)
from eazy_sdk_sqlmodel.tables import (
    TABLE_NAMES,
    Account,
    AccountEvent,
    AccountLink,
    Session,
    Verification,
)
from sqlalchemy import JSON, Boolean, Column, DateTime, MetaData, String, Table, Uuid, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlmodel import select


def _v1_metadata() -> MetaData:
    metadata = MetaData()

    def timestamps() -> tuple[Column[Any], Column[Any]]:
        return (
            Column("created_at", DateTime, nullable=False),
            Column("updated_at", DateTime, nullable=False),
        )

    Table(
        "eazy_sdk_accounts",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("identifier", String, nullable=False),
        Column("provider", String, nullable=True),
        Column("status", String, nullable=False),
        Column("cred_scheme", String),
        Column("cred_secret", String),
        Column("cred_data", JSON, nullable=False),
        Column("meta", JSON, nullable=False),
        *timestamps(),
    )
    Table(
        "eazy_sdk_sessions",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("account_id", Uuid, nullable=False),
        Column("scheme", String, nullable=False),
        Column("headers", JSON, nullable=False),
        Column("cookies", JSON, nullable=False),
        Column("params", JSON, nullable=False),
        Column("meta", JSON, nullable=False),
        Column("expires_at", DateTime),
        Column("scopes_list", JSON, nullable=False),
        Column("audience", String),
        Column("subject", String),
        Column("label", String),
        Column("is_active", Boolean, nullable=False),
        *timestamps(),
    )
    Table(
        "eazy_sdk_verifications",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("account_id", Uuid, nullable=False),
        Column("type", String, nullable=False),
        Column("status", String, nullable=False),
        Column("target", String),
        Column("meta", JSON, nullable=False),
        Column("verified_at", DateTime),
        *timestamps(),
    )
    Table(
        "eazy_sdk_restrictions",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("account_id", Uuid, nullable=False),
        Column("type", String, nullable=False),
        Column("status", String, nullable=False),
        Column("reason", String),
        Column("expires_at", DateTime),
        Column("meta", JSON, nullable=False),
        *timestamps(),
    )
    Table(
        "eazy_sdk_account_links",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("account_id", Uuid, nullable=False),
        Column("linked_account_id", Uuid, nullable=False),
        Column("type", String, nullable=False),
        Column("meta", JSON, nullable=False),
        *timestamps(),
    )
    Table(
        "eazy_sdk_account_events",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("account_id", Uuid, nullable=False),
        Column("type", String, nullable=False),
        Column("occurred_at", DateTime, nullable=False),
        Column("data", JSON, nullable=False),
        Column("session_id", Uuid),
        Column("meta", JSON, nullable=False),
    )
    return metadata


async def _create_v1(
    engine: AsyncEngine,
    *,
    duplicate: bool = False,
    restriction: bool = False,
) -> dict[str, UUID]:
    metadata = _v1_metadata()
    now = datetime(2026, 8, 15, tzinfo=UTC).replace(tzinfo=None)
    account_id = uuid4()
    resource_id = uuid4()
    session_id = uuid4()
    verification_id = uuid4()
    link_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        accounts = metadata.tables["eazy_sdk_accounts"]
        values = [
            {
                "id": account_id,
                "identifier": "ada@example.test",
                "provider": None,
                "status": "pending_verification",
                "cred_scheme": "password",
                "cred_secret": "correct-horse",
                "cred_data": {"email": "ada@example.test"},
                "meta": {
                    "registration": {
                        "remote_id": "remote-ada",
                        "profile": {"first_name": "Ada"},
                        "revision": 3,
                        "meta": {"source": "fixture"},
                    }
                },
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": resource_id,
                "identifier": "mail@example.test",
                "provider": "mail",
                "status": "active",
                "cred_scheme": "password",
                "cred_secret": "mail-secret",
                "cred_data": {},
                "meta": {},
                "created_at": now,
                "updated_at": now,
            },
        ]
        if duplicate:
            values.append({**values[0], "id": uuid4()})
        await connection.execute(accounts.insert(), values)
        await connection.execute(
            metadata.tables["eazy_sdk_sessions"].insert(),
            {
                "id": session_id,
                "account_id": account_id,
                "scheme": "bearer",
                "headers": {"Authorization": "Bearer access-v1"},
                "cookies": {},
                "params": {},
                "meta": {},
                "expires_at": now,
                "scopes_list": ["read"],
                "audience": "api",
                "subject": "ada",
                "label": "registration",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
        )
        await connection.execute(
            metadata.tables["eazy_sdk_verifications"].insert(),
            {
                "id": verification_id,
                "account_id": account_id,
                "type": "email_code",
                "status": "pending",
                "target": "a***@example.test",
                "meta": {
                    "challenge_id": "email-1",
                    "expires_at": now.isoformat(),
                    "attempts_remaining": 3,
                    "meta": {"provider": "legacy"},
                },
                "verified_at": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        await connection.execute(
            metadata.tables["eazy_sdk_account_links"].insert(),
            {
                "id": link_id,
                "account_id": account_id,
                "linked_account_id": resource_id,
                "type": "registered_via",
                "meta": {},
                "created_at": now,
                "updated_at": now,
            },
        )
        await connection.execute(
            metadata.tables["eazy_sdk_account_events"].insert(),
            {
                "id": uuid4(),
                "account_id": account_id,
                "type": "account.created",
                "occurred_at": now,
                "data": {},
                "session_id": session_id,
                "meta": {},
            },
        )
        if restriction:
            await connection.execute(
                metadata.tables["eazy_sdk_restrictions"].insert(),
                {
                    "id": uuid4(),
                    "account_id": account_id,
                    "type": "ban",
                    "status": "active",
                    "reason": "fixture",
                    "expires_at": None,
                    "meta": {},
                    "created_at": now,
                    "updated_at": now,
                },
            )
    return {
        "account": account_id,
        "resource": resource_id,
        "session": session_id,
        "verification": verification_id,
        "link": link_id,
    }


async def test_dry_run_reports_duplicates_and_restriction_policy(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conflict.db'}")
    try:
        await _create_v1(engine, duplicate=True, restriction=True)
        report = await inspect_v1(engine)
        assert {issue.code for issue in report.issues} == {
            "duplicate_identity",
            "duplicate_remote_id",
            "restriction_policy_required",
        }
        assert not report.can_apply
        with pytest.raises(MigrationConflictError):
            await migrate_v1_to_v2(engine)
        names = await _table_names(engine)
        assert "eazy_sdk_accounts" in names and "accounts" not in names
    finally:
        await engine.dispose()


async def test_populated_v1_fixture_migrates_once_without_dual_schema(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}")
    try:
        ids = await _create_v1(engine)
        report = await migrate_v1_to_v2(engine)
        assert report.can_apply
        assert await _table_names(engine) == TABLE_NAMES

        async with AsyncSession(engine) as connection:
            account = (
                (await connection.execute(select(Account).where(Account.id == ids["account"])))
                .scalars()
                .one()
            )
            assert account.provider == DEFAULT_PROVIDER
            assert account.remote_id == "remote-ada"
            assert account.revision == 3
            assert account.profile == {"first_name": "Ada"}
            assert account.credentials["data"]["secret"] == "correct-horse"
            stored_session = (
                (await connection.execute(select(Session).where(Session.id == ids["session"])))
                .scalars()
                .one()
            )
            assert stored_session.key == "registration"
            assert stored_session.payload["data"]["headers"] == {
                "Authorization": "Bearer access-v1"
            }
            verification = (
                (
                    await connection.execute(
                        select(Verification).where(Verification.id == ids["verification"])
                    )
                )
                .scalars()
                .one()
            )
            assert verification.challenge_id == "email-1"
            assert verification.attempts_remaining == 3
            link = (
                (await connection.execute(select(AccountLink).where(AccountLink.id == ids["link"])))
                .scalars()
                .one()
            )
            assert link.owner_account_id == ids["account"]
            assert link.resource_account_id == ids["resource"]
            assert link.exclusive_scope is None
            event = (await connection.execute(select(AccountEvent))).scalars().one()
            assert event.session_id == ids["session"]

        with pytest.raises(MigrationAlreadyAppliedError):
            await migrate_v1_to_v2(engine)
    finally:
        await engine.dispose()


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
