from collections.abc import AsyncIterator

import eazy_sdk_sqlmodel.tables  # noqa: F401  # register default tables in metadata
import pytest_asyncio
from eazy_sdk_sqlmodel import create_all
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    # fresh in-memory SQLite per fixture call → full isolation, no rollback wrapper needed
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(
        engine.sync_engine,
        "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    await create_all(engine)
    async with AsyncSession(engine, expire_on_commit=False) as sess:
        yield sess
    await engine.dispose()
