"""Store and select one account in the in-memory workspace."""

from __future__ import annotations

import asyncio

from eazy_sdk_accounts.storage import AccountWorkspace, MemoryStorage


async def main() -> None:
    # docs:integration-accounts:start
    # examples/docs/integration_accounts.py
    workspace = AccountWorkspace(MemoryStorage())
    account = await workspace.accounts.create(
        "ada@mail.example",
        provider="mail",
        meta={"display_name": "Ada"},
    )
    await workspace.verifications.mark(account, "email", target=account.identifier)
    selected = await workspace.pool.pick(provider="mail", requires_verified={"email"})
    # docs:integration-accounts:end
    print(f"account: {account.identifier}")
    print(f"selected: {selected.identifier if selected else None}")


if __name__ == "__main__":
    asyncio.run(main())
