"""Shared base for storage services."""

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
    Restriction,
    RestrictionRecord,
    Session,
    SessionRecord,
    Verification,
    VerificationRecord,
)
from eazy_sdk.storage.storage import Storage, StorageOp


class _StorageService[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
]:
    """Holds the Storage and runs StorageOps through the middleware chain."""

    def __init__(
        self,
        storage: Storage[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID],
    ) -> None:
        self.storage = storage

    async def _emit(self, op: StorageOp) -> None:
        async with self.storage.operation(op):
            pass
