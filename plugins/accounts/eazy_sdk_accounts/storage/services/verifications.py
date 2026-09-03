"""Per-account verification service (email/sms/kyc/...)."""

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
    _utcnow,
)
from eazy_sdk_accounts.storage.services.base import _StorageService
from eazy_sdk_accounts.storage.storage import StorageOp

VERIFIED = "verified"


class Verifications[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
](_StorageService[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID]):
    """Record and query account verifications by type."""

    async def set(
        self, account: Any, type: str, status: str, *, target: str | None = None
    ) -> VerificationT:
        data: dict[str, Any] = {"account_id": account.id, "type": type, "status": status}
        if target is not None:
            data["target"] = target
        if status == VERIFIED:
            data["verified_at"] = _utcnow()
        rec = await self.storage.verifications.create(data)
        await self._emit(
            StorageOp(
                "verification.set",
                account_id=account.id,
                entity=rec,
                data={"type": type, "status": status},
            )
        )
        return rec

    async def mark(self, account: Any, type: str, *, target: str | None = None) -> VerificationT:
        """Shortcut for set(..., status='verified')."""
        return await self.set(account, type, VERIFIED, target=target)

    async def is_verified(self, account: Any, type: str) -> bool:
        current = await self.storage.verifications.current(account.id, type)
        return current is not None and current.status == VERIFIED

    async def pending(self, account: Any) -> list[VerificationT]:
        """Return the current record for each type whose latest status is not 'verified'."""
        all_records = await self.storage.verifications.list_for_account(account.id)
        seen: set[str] = set()
        result: list[VerificationT] = []
        # list_for_account is sorted ascending; iterate in reverse to see latest first
        for rec in reversed(all_records):
            if rec.type in seen:
                continue
            seen.add(rec.type)
            if rec.status != VERIFIED:
                result.append(rec)
        return result
