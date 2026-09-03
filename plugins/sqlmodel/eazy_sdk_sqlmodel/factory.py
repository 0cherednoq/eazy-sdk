"""Factories for the five-table SQLModel account backend."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Table, UniqueConstraint, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from eazy_sdk.core.errors import ConfigurationError
from eazy_sdk_sqlmodel import tables
from eazy_sdk_sqlmodel.adapter import (
    SqlAccountRepository,
    SqlEventStore,
    SqlLinkRepository,
    SqlSessionRepository,
    SqlVerificationRepository,
)
from eazy_sdk_sqlmodel.models import normalize_identifier, normalize_provider


@dataclass(frozen=True)
class SqlStorageModels:
    """One mandatory v2 table class for each account aggregate component."""

    account: type[SQLModel] = tables.Account
    session: type[SQLModel] = tables.Session
    verification: type[SQLModel] = tables.Verification
    link: type[SQLModel] = tables.AccountLink
    event: type[SQLModel] = tables.AccountEvent


DEFAULT_MODELS = SqlStorageModels()


class SqlModelConfigurationError(ConfigurationError, TypeError):
    pass


@dataclass(frozen=True)
class SqlAccountStorage:
    accounts: SqlAccountRepository[Any]
    sessions: SqlSessionRepository[Any]
    verifications: SqlVerificationRepository[Any]
    links: SqlLinkRepository[Any]
    events: SqlEventStore[Any]


class SqlAccounts:
    """Short account operations with an atomic lifecycle event."""

    def __init__(self, storage: SqlAccountStorage) -> None:
        self._storage = storage

    async def create(
        self,
        identifier: str,
        *,
        provider: str | None = None,
        credentials: Mapping[str, object] | None = None,
        profile: Mapping[str, object] | None = None,
        status: str = "active",
        meta: Mapping[str, object] | None = None,
    ) -> Any:
        account = await self._storage.accounts.create(
            {
                "identifier": normalize_identifier(identifier),
                "provider": normalize_provider(provider),
                "credentials": dict(credentials or {}),
                "profile": dict(profile or {}),
                "status": status,
                "meta": dict(meta or {}),
            }
        )
        await self._storage.events.record({"account_id": account.id, "type": "account.created"})
        return account

    async def get(self, account_id: UUID) -> Any | None:
        return await self._storage.accounts.get(account_id)

    async def get_by_identifier(
        self, identifier: str, *, provider: str | None = None
    ) -> Any | None:
        return await self._storage.accounts.get_by_identifier(identifier, provider=provider)

    async def list(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Any]:
        return await self._storage.accounts.list(
            provider=provider, status=status, limit=limit, offset=offset
        )


@dataclass(frozen=True)
class SqlAccountWorkspace:
    storage: SqlAccountStorage
    accounts: SqlAccounts

    @property
    def sessions(self) -> SqlSessionRepository[Any]:
        return self.storage.sessions

    @property
    def verifications(self) -> SqlVerificationRepository[Any]:
        return self.storage.verifications

    @property
    def links(self) -> SqlLinkRepository[Any]:
        return self.storage.links

    @property
    def events(self) -> SqlEventStore[Any]:
        return self.storage.events


def build_sqlmodel_storage(
    session: AsyncSession,
    *,
    models: SqlStorageModels = DEFAULT_MODELS,
) -> SqlAccountStorage:
    _validate_models(models)
    return SqlAccountStorage(
        accounts=SqlAccountRepository(session, models.account),
        sessions=SqlSessionRepository(session, models.session),
        verifications=SqlVerificationRepository(session, models.verification),
        links=SqlLinkRepository(session, models.link),
        events=SqlEventStore(session, models.event),
    )


def _metadata(models: SqlStorageModels) -> Any:
    tables_to_create = [
        model.__table__  # type: ignore[attr-defined]
        for model in (
            models.account,
            models.session,
            models.verification,
            models.link,
            models.event,
        )
    ]
    return tables_to_create


async def create_all(engine: AsyncEngine, *, models: SqlStorageModels = DEFAULT_MODELS) -> None:
    """Quickstart/test helper that creates only the five configured v2 tables."""

    _validate_models(models)
    if engine.dialect.name == "sqlite":
        event.listen(
            engine.sync_engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
            once=True,
        )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: SQLModel.metadata.create_all(sync, tables=_metadata(models))
        )


def _validate_models(models: SqlStorageModels) -> None:
    configured = {
        "account": _model_table(models.account),
        "session": _model_table(models.session),
        "verification": _model_table(models.verification),
        "link": _model_table(models.link),
        "event": _model_table(models.event),
    }
    required_columns = {
        "account": {
            "id",
            "provider",
            "identifier",
            "remote_id",
            "status",
            "revision",
            "credentials",
            "profile",
            "meta",
            "created_at",
            "updated_at",
        },
        "session": {
            "id",
            "account_id",
            "key",
            "revision",
            "kind",
            "payload",
            "expires_at",
            "is_active",
            "created_at",
            "updated_at",
        },
        "verification": {
            "id",
            "account_id",
            "via_account_id",
            "challenge_id",
            "kind",
            "status",
            "target",
            "expires_at",
            "attempts_remaining",
            "replaces_id",
            "meta",
            "created_at",
            "updated_at",
            "verified_at",
        },
        "link": {
            "id",
            "owner_account_id",
            "resource_account_id",
            "relation",
            "status",
            "exclusive_scope",
            "meta",
            "created_at",
            "updated_at",
            "released_at",
        },
        "event": {
            "id",
            "account_id",
            "type",
            "occurred_at",
            "correlation_id",
            "session_id",
            "verification_id",
            "link_id",
            "data",
            "meta",
        },
    }
    for name, required in required_columns.items():
        table = configured[name]
        missing = sorted(required - set(table.c.keys()))
        if missing:
            raise SqlModelConfigurationError(
                f"configured {name} model is missing mandatory columns: {', '.join(missing)}"
            )

    unique_sets = {
        "account": ({"provider", "identifier"}, {"provider", "remote_id"}),
        "session": ({"account_id", "key"},),
        "verification": ({"account_id", "challenge_id"},),
        "link": (
            {"owner_account_id", "resource_account_id", "relation"},
            {"resource_account_id", "exclusive_scope"},
        ),
    }
    for name, unique_requirements in unique_sets.items():
        unique_actual = _unique_column_sets(configured[name])
        for columns in unique_requirements:
            if frozenset(columns) not in unique_actual:
                raise SqlModelConfigurationError(
                    f"configured {name} model is missing unique constraint/index on "
                    f"{', '.join(sorted(columns))}"
                )

    account_table = configured["account"].name
    expected_foreign_keys = {
        "session": {"account_id": (account_table, "CASCADE")},
        "verification": {
            "account_id": (account_table, "CASCADE"),
            "via_account_id": (account_table, "SET NULL"),
            "replaces_id": (configured["verification"].name, "SET NULL"),
        },
        "link": {
            "owner_account_id": (account_table, "RESTRICT"),
            "resource_account_id": (account_table, "RESTRICT"),
        },
        "event": {
            "account_id": (account_table, "CASCADE"),
            "session_id": (configured["session"].name, "SET NULL"),
            "verification_id": (configured["verification"].name, "SET NULL"),
            "link_id": (configured["link"].name, "SET NULL"),
        },
    }
    for name, expectations in expected_foreign_keys.items():
        foreign_key_actual = {
            foreign_key.parent.name: (
                foreign_key.column.table.name,
                (foreign_key.ondelete or "").upper(),
            )
            for foreign_key in configured[name].foreign_keys
        }
        for column, expected in expectations.items():
            if foreign_key_actual.get(column) != expected:
                raise SqlModelConfigurationError(
                    f"configured {name}.{column} must reference {expected[0]} "
                    f"with ON DELETE {expected[1]}"
                )


def _model_table(model: type[SQLModel]) -> Table:
    table = getattr(model, "__table__", None)
    if not isinstance(table, Table):
        raise SqlModelConfigurationError("configured SQLModel must be a concrete table model")
    return table


def _unique_column_sets(table: Table) -> set[frozenset[str]]:
    result = {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    result.update(
        frozenset(column.name for column in index.columns)
        for index in table.indexes
        if index.unique
    )
    return result


@asynccontextmanager
async def open_workspace(
    engine: AsyncEngine,
    *,
    models: SqlStorageModels = DEFAULT_MODELS,
) -> AsyncIterator[SqlAccountWorkspace]:
    """Open a caller-engine-owned atomic five-table account workspace."""

    await create_all(engine, models=models)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        storage = build_sqlmodel_storage(session, models=models)
        workspace = SqlAccountWorkspace(storage=storage, accounts=SqlAccounts(storage))
        try:
            yield workspace
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


__all__ = [
    "DEFAULT_MODELS",
    "SqlAccountStorage",
    "SqlAccountWorkspace",
    "SqlAccounts",
    "SqlModelConfigurationError",
    "SqlStorageModels",
    "build_sqlmodel_storage",
    "create_all",
    "open_workspace",
]
