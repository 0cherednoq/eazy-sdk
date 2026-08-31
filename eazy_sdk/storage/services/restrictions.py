"""Account restriction service (ban / freeze / limit / ...)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from eazy_sdk.storage.entities import (
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
    _utcnow,
)
from eazy_sdk.storage.services.base import _StorageService
from eazy_sdk.storage.storage import StorageOp

BAN = "ban"
FREEZE = "freeze"
ACTIVE = "active"
LIFTED = "lifted"


class Restrictions[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
](_StorageService[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID]):
    """Add, lift, and query account restrictions."""

    async def restrict(
        self,
        account: Any,
        type: str,
        *,
        reason: str | None = None,
        until: datetime | None = None,
    ) -> RestrictionT:
        data: dict[str, Any] = {"account_id": account.id, "type": type, "status": ACTIVE}
        if reason is not None:
            data["reason"] = reason
        if until is not None:
            data["expires_at"] = until
        rec = await self.storage.restrictions.create(data)
        await self._emit(
            StorageOp("restriction.add", account_id=account.id, entity=rec, data={"type": type})
        )
        return rec

    async def ban(
        self, account: Any, *, reason: str | None = None, until: datetime | None = None
    ) -> RestrictionT:
        """Shortcut for restrict(..., type='ban')."""
        return await self.restrict(account, BAN, reason=reason, until=until)

    async def freeze(
        self, account: Any, *, reason: str | None = None, until: datetime | None = None
    ) -> RestrictionT:
        """Shortcut for restrict(..., type='freeze')."""
        return await self.restrict(account, FREEZE, reason=reason, until=until)

    async def active(self, account: Any) -> list[RestrictionT]:
        """Return all non-expired active restrictions for the account."""
        now = _utcnow()
        return [
            r
            for r in await self.storage.restrictions.list_for_account(account.id, status=ACTIVE)
            if r.expires_at is None or r.expires_at > now
        ]

    async def lift(self, account: Any, *, type: str | None = None) -> None:
        """Flip active restrictions to 'lifted' (optionally filtered by type)."""
        for r in await self.storage.restrictions.list_for_account(account.id, status=ACTIVE):
            if type is None or r.type == type:
                await self.storage.restrictions.update(r, {"status": LIFTED})
        await self._emit(StorageOp("restriction.lift", account_id=account.id, data={"type": type}))

    async def is_banned(self, account: Any) -> bool:
        """Return True if the account has an active, non-expired ban."""
        return any(r.type == BAN for r in await self.active(account))

    async def is_frozen(self, account: Any) -> bool:
        """Return True if the account has an active, non-expired freeze."""
        return any(r.type == FREEZE for r in await self.active(account))
