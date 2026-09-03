from __future__ import annotations

import asyncio
import os
from typing import Any, cast

import pytest
from eazy_sdk_accounts.storage.exceptions import DuplicateAccountError
from eazy_sdk_sqlmodel import (
    DEFAULT_PROVIDER,
    SqlResourceConflictError,
    build_sqlmodel_storage,
    create_all,
)
from eazy_sdk_sqlmodel.tables import (
    TABLE_NAMES,
    Account,
    AccountEvent,
    AccountLink,
    Session,
    Verification,
)
from sqlalchemy import Table, inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

POSTGRES_DSN = os.environ.get("EAZY_SDK_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    POSTGRES_DSN is None,
    reason="EAZY_SDK_POSTGRES_DSN is not configured",
)


def _tables() -> list[Table]:
    return [
        cast(Table, cast(Any, model).__table__)
        for model in (Account, Session, Verification, AccountLink, AccountEvent)
    ]


async def test_postgresql_schema_uniqueness_cas_and_reservation_concurrency() -> None:
    assert POSTGRES_DSN is not None
    engine = create_async_engine(POSTGRES_DSN)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: SQLModel.metadata.drop_all(sync, tables=list(reversed(_tables())))
            )
        await create_all(engine)
        async with engine.connect() as connection:
            names = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names(schema="public"))
            )
            assert names == TABLE_NAMES

        async with AsyncSession(engine, expire_on_commit=False) as setup:
            accounts = build_sqlmodel_storage(setup).accounts
            owner1 = await accounts.create({"identifier": "owner-1", "provider": None})
            owner2 = await accounts.create({"identifier": "owner-2", "provider": None})
            resource = await accounts.create({"identifier": "resource", "provider": "mail"})
            await setup.commit()
            ids = owner1.id, owner2.id, resource.id
            assert owner1.provider == DEFAULT_PROVIDER
            updated = await accounts.compare_and_swap(owner1.id, 0, {"status": "active"})
            assert updated.revision == 1
            await setup.commit()

        async def reserve(owner_id) -> bool:  # type: ignore[no-untyped-def]
            async with AsyncSession(engine) as database:
                links = build_sqlmodel_storage(database).links
                try:
                    await links.reserve(
                        owner_account_id=owner_id,
                        resource_account_id=ids[2],
                        relation="registration_identity",
                        exclusive_scope="registration",
                    )
                    await database.commit()
                    return True
                except SqlResourceConflictError:
                    await database.rollback()
                    return False

        assert sorted(await asyncio.gather(reserve(ids[0]), reserve(ids[1]))) == [False, True]

        async with AsyncSession(engine) as duplicate:
            with pytest.raises(DuplicateAccountError):
                await build_sqlmodel_storage(duplicate).accounts.create(
                    {"identifier": "owner-1", "provider": None}
                )
            await duplicate.rollback()
            assert len((await duplicate.execute(select(Account))).scalars().all()) == 3
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: SQLModel.metadata.drop_all(sync, tables=list(reversed(_tables())))
            )
        await engine.dispose()
