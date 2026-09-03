"""Account-to-account linking service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from eazy_sdk_accounts.storage.entities import (
    Account,
    AccountEvent,
    AccountEventRecord,
    AccountLink,
    AccountLinkRecord,
    AccountRecord,
    Restriction,
    RestrictionRecord,
    Session,
    SessionRecord,
    Verification,
    VerificationRecord,
)
from eazy_sdk_accounts.storage.services.base import _StorageService
from eazy_sdk_accounts.storage.storage import StorageOp


class AccountLinks[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
](_StorageService[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID]):
    """Create, remove, and resolve typed links between accounts."""

    async def add(self, account: Any, *, to: Any, type: str) -> LinkT:
        """Create a directed link from *account* to *to* with the given *type*."""
        link = await self.storage.links.create(
            {"account_id": account.id, "linked_account_id": to.id, "type": type}
        )
        await self._emit(
            StorageOp("link.add", account_id=account.id, entity=link, data={"type": type})
        )
        return link

    async def remove(self, account: Any, *, to: Any, type: str | None = None) -> None:
        """Delete all links from *account* to *to*, optionally filtered by *type*."""
        for link in await self.storage.links.list_for_account(account.id, type=type):
            if link.linked_account_id == to.id:
                await self.storage.links.delete(link)
        await self._emit(StorageOp("link.remove", account_id=account.id, data={"type": type}))

    async def of(self, account: Any, *, type: str | None = None) -> list[AccountT]:
        """Return accounts that *account* links to, optionally filtered by *type*.

        Links whose target account no longer exists are silently skipped.
        """
        out: list[AccountT] = []
        for link in await self.storage.links.list_for_account(account.id, type=type):
            linked = await self.storage.accounts.get(link.linked_account_id)
            if linked is not None:
                out.append(linked)
        return out
