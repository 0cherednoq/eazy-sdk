"""Account event-log service: record events and answer typical questions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from eazy_sdk_accounts.storage.entities import (
    Account,
    AccountEvent,
    AccountEventRecord,
    AccountEventType,
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


class History[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
](_StorageService[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID]):
    """Append events and aggregate the account's timeline."""

    async def record(self, account: Any, type: str, **data: Any) -> EventT:
        return await self.storage.events.create(
            {"account_id": account.id, "type": type, "data": dict(data)}
        )

    async def last_login(self, account: Any) -> datetime | None:
        event = await self.storage.events.last(account.id, AccountEventType.LOGIN_SUCCESS)
        return event.occurred_at if event is not None else None

    async def failed_login_count(self, account: Any, *, since: datetime | None = None) -> int:
        return await self.storage.events.count(
            account.id, AccountEventType.LOGIN_FAILED, since=since
        )

    async def banned_at(self, account: Any) -> datetime | None:
        event = await self.storage.events.last(account.id, AccountEventType.BANNED)
        return event.occurred_at if event is not None else None

    async def timeline(
        self,
        account: Any,
        *,
        type: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        order: Literal["asc", "desc"] = "desc",
    ) -> list[EventT]:
        return await self.storage.events.list_for_account(
            account.id, type=type, since=since, limit=limit, order=order
        )
