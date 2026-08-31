"""Account CRUD / lookup service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from eazy_sdk.storage.entities import (
    Account,
    AccountEvent,
    AccountEventRecord,
    AccountLink,
    AccountLinkRecord,
    AccountRecord,
    AccountStatus,
    Credentials,
    Restriction,
    RestrictionRecord,
    Session,
    SessionRecord,
    Verification,
    VerificationRecord,
)
from eazy_sdk.storage.services.base import _StorageService
from eazy_sdk.storage.storage import StorageOp


class Accounts[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
](_StorageService[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID]):
    """Create, look up, update, and soft-delete accounts."""

    async def create(
        self,
        identifier: str,
        *,
        provider: str | None = None,
        credentials: Credentials | None = None,
        status: str = AccountStatus.ACTIVE,
        meta: dict[str, Any] | None = None,
        **fields: Any,
    ) -> AccountT:
        data: dict[str, Any] = {"identifier": identifier, "provider": provider, "status": status}
        if credentials is not None:
            data["credentials"] = credentials
        if meta is not None:
            data["meta"] = meta
        data.update(fields)
        acc = await self.storage.accounts.create(data)
        await self._emit(
            StorageOp(
                "account.create",
                account_id=acc.id,
                entity=acc,
                data={"identifier": identifier, "provider": provider},
            )
        )
        return acc

    async def get(self, account_id: Any) -> AccountT | None:
        return await self.storage.accounts.get(account_id)

    async def get_by_identifier(
        self, identifier: str, *, provider: str | None = None
    ) -> AccountT | None:
        return await self.storage.accounts.get_by_identifier(identifier, provider=provider)

    async def list(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AccountT]:
        return await self.storage.accounts.list(
            provider=provider, status=status, limit=limit, offset=offset
        )

    async def get_or_create(
        self, identifier: str, *, provider: str | None = None, **fields: Any
    ) -> AccountT:
        existing = await self.storage.accounts.get_by_identifier(identifier, provider=provider)
        if existing is not None:
            return existing
        return await self.create(identifier, provider=provider, **fields)

    async def update(self, account: AccountT, **changes: Any) -> AccountT:
        acc = await self.storage.accounts.update(account, changes)
        safe_data = {k: v for k, v in changes.items() if k != "credentials"}
        await self._emit(StorageOp("account.update", account_id=acc.id, entity=acc, data=safe_data))
        return acc

    async def set_status(self, account: AccountT, status: str) -> AccountT:
        return await self.update(account, status=status)

    async def delete(self, account: AccountT) -> AccountT:
        """Soft-delete: mark status='deleted' (records are retained for history)."""
        return await self.set_status(account, AccountStatus.DELETED)
