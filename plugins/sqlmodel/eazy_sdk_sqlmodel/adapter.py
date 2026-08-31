"""SQLModel account-storage v2 repositories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select

from eazy_sdk.storage.exceptions import DuplicateAccountError
from eazy_sdk_sqlmodel.models import (
    normalize_identifier,
    normalize_provider,
    utcnow,
)


class SqlStorageError(RuntimeError):
    pass


class SqlConcurrencyError(SqlStorageError):
    pass


class SqlResourceConflictError(SqlStorageError):
    def __init__(self, resource_account_id: UUID, exclusive_scope: str) -> None:
        self.resource_account_id = resource_account_id
        self.exclusive_scope = exclusive_scope
        super().__init__(
            f"account resource {resource_account_id} is already reserved in "
            f"scope {exclusive_scope!r}"
        )


class _SqlRepository[ModelT: SQLModel]:
    """Shared async reads over caller-owned AsyncSession."""

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    async def get(self, id: Any) -> ModelT | None:
        return await self._session.get(self._model, id)

    async def _add(self, obj: ModelT) -> ModelT:
        self._session.add(obj)
        await self._session.flush()
        return obj


class SqlAccountRepository[AccountT: SQLModel](_SqlRepository[AccountT]):
    async def get_by_identifier(
        self, identifier: str, *, provider: str | None = None
    ) -> AccountT | None:
        model = cast(Any, self._model)
        statement = select(model).where(
            model.identifier == normalize_identifier(identifier),
            model.provider == normalize_provider(provider),
        )
        return cast(AccountT | None, (await self._session.execute(statement)).scalars().first())

    async def create(self, data: Mapping[str, Any]) -> AccountT:
        columns = dict(data)
        columns["identifier"] = normalize_identifier(cast(str, columns["identifier"]))
        columns["provider"] = normalize_provider(cast(str | None, columns.get("provider")))
        try:
            return await self._add(self._model(**columns))
        except IntegrityError as exc:
            raise DuplicateAccountError(
                "account already exists",
                op="account.create",
                identifier=columns["identifier"],
                provider=columns["provider"],
            ) from exc

    async def compare_and_swap(
        self,
        account_id: UUID,
        expected_revision: int,
        data: Mapping[str, Any],
    ) -> AccountT:
        table = cast(Any, self._model).__table__
        columns = dict(data)
        columns.pop("id", None)
        columns.pop("revision", None)
        columns["revision"] = table.c.revision + 1
        columns["updated_at"] = utcnow()
        result = cast(
            CursorResult[tuple[object, ...]],
            await self._session.execute(
                update(table)
                .where(table.c.id == account_id, table.c.revision == expected_revision)
                .values(**columns)
            ),
        )
        if result.rowcount != 1:
            raise SqlConcurrencyError("account revision changed")
        refreshed = await self.get(account_id)
        if refreshed is None:
            raise SqlConcurrencyError("account disappeared during compare-and-swap")
        await self._session.refresh(refreshed)
        return refreshed

    async def list(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AccountT]:
        model = cast(Any, self._model)
        statement = select(model)
        if provider is not None:
            statement = statement.where(model.provider == normalize_provider(provider))
        if status is not None:
            statement = statement.where(model.status == status)
        statement = statement.order_by(model.created_at).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())


class SqlSessionRepository[SessionT: SQLModel](_SqlRepository[SessionT]):
    async def get_by_key(self, account_id: UUID, key: str) -> SessionT | None:
        model = cast(Any, self._model)
        statement = select(model).where(model.account_id == account_id, model.key == key)
        return cast(SessionT | None, (await self._session.execute(statement)).scalars().first())

    async def create(self, data: Mapping[str, Any]) -> SessionT:
        return await self._add(self._model(**dict(data)))

    async def compare_and_swap(
        self,
        session_id: UUID,
        expected_revision: int,
        data: Mapping[str, Any],
    ) -> SessionT:
        table = cast(Any, self._model).__table__
        columns = dict(data)
        columns.pop("id", None)
        columns.pop("revision", None)
        columns["revision"] = table.c.revision + 1
        columns["updated_at"] = utcnow()
        result = cast(
            CursorResult[tuple[object, ...]],
            await self._session.execute(
                update(table)
                .where(table.c.id == session_id, table.c.revision == expected_revision)
                .values(**columns)
            ),
        )
        if result.rowcount != 1:
            raise SqlConcurrencyError("session revision changed")
        refreshed = await self.get(session_id)
        if refreshed is None:
            raise SqlConcurrencyError("session disappeared during compare-and-swap")
        await self._session.refresh(refreshed)
        return refreshed

    async def list_for_account(
        self,
        account_id: UUID,
        *,
        active: bool | None = None,
        key: str | None = None,
    ) -> list[SessionT]:
        model = cast(Any, self._model)
        statement = select(model).where(model.account_id == account_id)
        if active is not None:
            statement = statement.where(model.is_active == active)
        if key is not None:
            statement = statement.where(model.key == key)
        statement = statement.order_by(model.created_at)
        return list((await self._session.execute(statement)).scalars().all())


class SqlVerificationRepository[VerificationT: SQLModel](_SqlRepository[VerificationT]):
    async def create(self, data: Mapping[str, Any]) -> VerificationT:
        return await self._add(self._model(**dict(data)))

    async def list_for_account(
        self,
        account_id: UUID,
        *,
        kind: str | None = None,
        status: str | None = None,
    ) -> list[VerificationT]:
        model = cast(Any, self._model)
        statement = select(model).where(model.account_id == account_id)
        if kind is not None:
            statement = statement.where(model.kind == kind)
        if status is not None:
            statement = statement.where(model.status == status)
        statement = statement.order_by(model.created_at)
        return list((await self._session.execute(statement)).scalars().all())

    async def current(self, account_id: UUID, kind: str | None = None) -> VerificationT | None:
        items = await self.list_for_account(account_id, kind=kind, status="pending")
        return items[-1] if items else None


class SqlLinkRepository[LinkT: SQLModel](_SqlRepository[LinkT]):
    async def reserve(
        self,
        *,
        owner_account_id: UUID,
        resource_account_id: UUID,
        relation: str,
        exclusive_scope: str | None,
        meta: Mapping[str, object] | None = None,
    ) -> LinkT:
        model = cast(Any, self._model)
        statement = select(model).where(
            model.owner_account_id == owner_account_id,
            model.resource_account_id == resource_account_id,
            model.relation == relation,
        )
        existing = cast(
            LinkT | None,
            (await self._session.execute(statement)).scalars().first(),
        )
        if existing is not None:
            cast(Any, existing).status = "active"
            cast(Any, existing).exclusive_scope = exclusive_scope
            cast(Any, existing).released_at = None
            cast(Any, existing).updated_at = utcnow()
            cast(Any, existing).meta = dict(meta or {})
            try:
                await self._session.flush()
            except IntegrityError as exc:
                raise SqlResourceConflictError(resource_account_id, exclusive_scope or "") from exc
            return existing
        row = self._model(
            owner_account_id=owner_account_id,
            resource_account_id=resource_account_id,
            relation=relation,
            exclusive_scope=exclusive_scope,
            meta=dict(meta or {}),
        )
        try:
            return await self._add(row)
        except IntegrityError as exc:
            if exclusive_scope is not None:
                raise SqlResourceConflictError(resource_account_id, exclusive_scope) from exc
            raise

    async def release(self, link: LinkT) -> LinkT:
        row = cast(Any, link)
        row.status = "released"
        row.exclusive_scope = None
        row.released_at = utcnow()
        row.updated_at = row.released_at
        await self._session.flush()
        return link

    async def list_for_owner(
        self,
        owner_account_id: UUID,
        *,
        relation: str | None = None,
        status: str | None = None,
    ) -> list[LinkT]:
        model = cast(Any, self._model)
        statement = select(model).where(model.owner_account_id == owner_account_id)
        if relation is not None:
            statement = statement.where(model.relation == relation)
        if status is not None:
            statement = statement.where(model.status == status)
        statement = statement.order_by(model.created_at)
        return list((await self._session.execute(statement)).scalars().all())

    async def reserve_many(
        self,
        owner_account_id: UUID,
        resources: Sequence[tuple[UUID, str, str | None, Mapping[str, object]]],
    ) -> list[LinkT]:
        rows: list[LinkT] = []
        for resource_account_id, relation, exclusive_scope, meta in resources:
            rows.append(
                await self.reserve(
                    owner_account_id=owner_account_id,
                    resource_account_id=resource_account_id,
                    relation=relation,
                    exclusive_scope=exclusive_scope,
                    meta=meta,
                )
            )
        return rows


class SqlEventStore[EventT: SQLModel](_SqlRepository[EventT]):
    """Append-only event store: no public update/delete methods exist."""

    async def append(self, event: EventT) -> EventT:
        return await self._add(event)

    async def record(self, data: Mapping[str, Any]) -> EventT:
        return await self.append(self._model(**dict(data)))

    async def list_for_account(
        self,
        account_id: UUID,
        *,
        type: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        order: Literal["asc", "desc"] = "desc",
    ) -> list[EventT]:
        model = cast(Any, self._model)
        statement = select(model).where(model.account_id == account_id)
        if type is not None:
            statement = statement.where(model.type == type)
        if since is not None:
            statement = statement.where(model.occurred_at >= since)
        column = model.occurred_at
        statement = statement.order_by(column.desc() if order == "desc" else column.asc())
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def last(self, account_id: UUID, type: str) -> EventT | None:
        items = await self.list_for_account(account_id, type=type, order="desc", limit=1)
        return items[0] if items else None


__all__ = [
    "SqlAccountRepository",
    "SqlConcurrencyError",
    "SqlEventStore",
    "SqlLinkRepository",
    "SqlResourceConflictError",
    "SqlSessionRepository",
    "SqlStorageError",
    "SqlVerificationRepository",
]
