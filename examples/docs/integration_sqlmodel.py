"""Persist one account in an in-memory SQLite workspace."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from eazy_sdk_sqlmodel import open_workspace


async def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        # docs:integration-sqlmodel:start
        # examples/docs/integration_sqlmodel.py
        async with open_workspace(engine) as workspace:
            account = await workspace.accounts.create(
                "ada@mail.example",
                provider="mail",
                profile={"display_name": "Ada"},
            )
        # docs:integration-sqlmodel:end
    finally:
        await engine.dispose()
    print(f"stored: {account.identifier}")


if __name__ == "__main__":
    asyncio.run(main())
