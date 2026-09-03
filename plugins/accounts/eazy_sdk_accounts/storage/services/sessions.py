"""Live-session service: save/read/invalidate transport sessions."""

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
    SessionData,
    SessionRecord,
    Verification,
    VerificationRecord,
    session_record_values,
)
from eazy_sdk_accounts.storage.services.base import _StorageService
from eazy_sdk_accounts.storage.storage import StorageOp


class Sessions[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
](_StorageService[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID]):
    """Persist and retrieve an account's live auth sessions."""

    async def save(self, account: Any, state: SessionData, *, label: str | None = None) -> SessionT:
        data = session_record_values(account.id, state, label=label)
        existing = await self.storage.sessions.get_active(account.id, label=label)
        if existing is not None:
            session = await self.storage.sessions.update(existing, data)
        else:
            session = await self.storage.sessions.create(data)
        await self._emit(
            StorageOp("session.save", account_id=account.id, entity=session, data={"label": label})
        )
        return session

    async def active(self, account: Any, *, label: str | None = None) -> SessionT | None:
        return await self.storage.sessions.get_active(account.id, label=label)

    async def invalidate(self, account: Any, *, label: str | None = None) -> None:
        for session in await self.storage.sessions.list_for_account(
            account.id, active=True, label=label
        ):
            await self.storage.sessions.update(session, {"is_active": False})
        await self._emit(
            StorageOp("session.invalidate", account_id=account.id, data={"label": label})
        )

    async def clear(self, account: Any) -> None:
        for session in await self.storage.sessions.list_for_account(account.id):
            await self.storage.sessions.delete(session)
        await self._emit(StorageOp("session.clear", account_id=account.id, data={}))
