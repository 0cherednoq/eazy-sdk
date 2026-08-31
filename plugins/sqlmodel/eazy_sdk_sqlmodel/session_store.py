"""Neutral SessionStore implementation over the v2 sessions table."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.session import SessionTransactionOrigin
from sqlmodel import select

from eazy_sdk.accounts.session import (
    SessionKey,
    SessionRevision,
    SessionRevisionError,
    StoredSession,
)
from eazy_sdk_sqlmodel.codecs import PlainPydanticCodec, SqlValueCodec
from eazy_sdk_sqlmodel.factory import DEFAULT_MODELS, SqlStorageModels
from eazy_sdk_sqlmodel.models import utcnow


class SqlSessionStore[TSession: BaseModel]:
    """Persist one account's auth/session lifecycle with database CAS revisions."""

    def __init__(
        self,
        session: AsyncSession,
        account_id: UUID,
        *,
        session_model: type[TSession],
        codec: SqlValueCodec[TSession] | None = None,
        kind: str = "custom",
        models: SqlStorageModels = DEFAULT_MODELS,
    ) -> None:
        self._session = session
        self._account_id = account_id
        self._codec = codec or PlainPydanticCodec(session_model)
        self._kind = kind
        self._models = models

    async def load(self, key: SessionKey) -> StoredSession[TSession] | None:
        row = await self._row(key)
        if row is None or not row.is_active:
            return None
        return StoredSession(
            self._codec.decode(cast(Mapping[str, object], row.payload)),
            SessionRevision(row.revision),
        )

    async def save(
        self,
        key: SessionKey,
        value: TSession,
        revision: SessionRevision,
    ) -> None:
        payload = self._codec.encode(value)
        transaction = self._transaction()
        async with transaction:
            row = await self._row(key)
            if row is None:
                row = cast(Any, self._models.session)(
                    account_id=self._account_id,
                    key=key.value,
                    revision=revision.value,
                    kind=self._kind,
                    payload=dict(payload),
                    expires_at=getattr(value, "expires_at", None),
                    is_active=True,
                )
                self._session.add(row)
                await self._session.flush()
                event_type = "session.created"
            else:
                expected = revision.value - 1
                if row.revision != expected:
                    raise SessionRevisionError("session revision changed")
                table = cast(Any, self._models.session).__table__
                result = cast(
                    CursorResult[tuple[object, ...]],
                    await self._session.execute(
                        update(table)
                        .where(table.c.id == row.id, table.c.revision == expected)
                        .values(
                            payload=dict(payload),
                            revision=revision.value,
                            expires_at=getattr(value, "expires_at", None),
                            is_active=True,
                            updated_at=utcnow(),
                        )
                    ),
                )
                if result.rowcount != 1:
                    raise SessionRevisionError("session revision changed")
                await self._session.refresh(row)
                event_type = "session.refreshed"
            self._session.add(
                cast(Any, self._models.event)(
                    account_id=self._account_id,
                    type=event_type,
                    session_id=row.id,
                    data={"key": key.value, "revision": row.revision},
                )
            )
            await self._session.flush()

    async def invalidate(
        self,
        key: SessionKey,
        expected: SessionRevision | None = None,
    ) -> None:
        transaction = self._transaction()
        async with transaction:
            row = await self._row(key)
            if row is None or not row.is_active:
                return
            if expected is not None and row.revision != expected.value:
                return
            selected_revision = row.revision
            table = cast(Any, self._models.session).__table__
            result = cast(
                CursorResult[tuple[object, ...]],
                await self._session.execute(
                    update(table)
                    .where(
                        table.c.id == row.id,
                        table.c.revision == selected_revision,
                        table.c.is_active.is_(True),
                    )
                    .values(
                        revision=table.c.revision + 1,
                        is_active=False,
                        updated_at=utcnow(),
                    )
                ),
            )
            if result.rowcount != 1:
                raise SessionRevisionError("session revision changed")
            await self._session.refresh(row)
            self._session.add(
                cast(Any, self._models.event)(
                    account_id=self._account_id,
                    type="session.invalidated",
                    session_id=row.id,
                    data={"key": key.value, "revision": row.revision},
                )
            )
            await self._session.flush()

    async def _row(self, key: SessionKey) -> Any | None:
        model = cast(Any, self._models.session)
        statement = select(model).where(
            model.account_id == self._account_id,
            model.key == key.value,
        )
        return (await self._session.execute(statement)).scalars().first()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        root = self._session.get_transaction()
        sync_root = root.sync_transaction if root is not None else None
        if root is not None and (
            sync_root is None or sync_root.origin is not SessionTransactionOrigin.AUTOBEGIN
        ):
            raise RuntimeError("SqlSessionStore cannot run inside an explicit outer transaction")
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


__all__ = ["SqlSessionStore"]
