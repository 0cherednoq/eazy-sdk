"""Account pool: usability filter, rotation, and in-process leasing."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
    AccountStatus,
    Restriction,
    RestrictionRecord,
    Session,
    SessionRecord,
    Verification,
    VerificationRecord,
)
from eazy_sdk_accounts.storage.exceptions import AccountLeasedError
from eazy_sdk_accounts.storage.services.base import _StorageService
from eazy_sdk_accounts.storage.services.restrictions import BAN as _BAN
from eazy_sdk_accounts.storage.services.restrictions import FREEZE as _FREEZE
from eazy_sdk_accounts.storage.services.verifications import VERIFIED as _VERIFIED
from eazy_sdk_accounts.storage.storage import Storage

_MIN_TIME = datetime.min.replace(tzinfo=UTC)

type Order = Literal["round_robin", "least_recently_used", "random"]


class AccountPool[
    AccountT: Account[Any] = AccountRecord,
    SessionT: Session[Any] = SessionRecord,
    VerificationT: Verification[Any] = VerificationRecord,
    RestrictionT: Restriction[Any] = RestrictionRecord,
    LinkT: AccountLink[Any] = AccountLinkRecord,
    EventT: AccountEvent[Any] = AccountEventRecord,
    ID = UUID,
](_StorageService[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID]):
    """Select usable accounts from a pool, with rotation and leasing."""

    def __init__(
        self,
        storage: Storage[AccountT, SessionT, VerificationT, RestrictionT, LinkT, EventT, ID],
    ) -> None:
        super().__init__(storage)
        self._cursor = 0
        self._leased: set[Any] = set()

    async def is_usable(self, account: Any) -> bool:
        """Return True iff the account is active and has no active ban or freeze."""
        if account.status != AccountStatus.ACTIVE:
            return False
        now = datetime.now(UTC)
        for r in await self.storage.restrictions.list_for_account(account.id, status="active"):
            if (r.expires_at is None or r.expires_at > now) and r.type in (_BAN, _FREEZE):
                return False
        return True

    async def list_usable(self, *, provider: str | None = None) -> list[AccountT]:
        """Return all usable (active, unrestricted) accounts.

        Leased accounts are NOT filtered here — only ``pick()`` skips leased ones.
        """
        usable: list[AccountT] = []
        # NOTE: is_usable issues one restrictions query per account (N+1)
        # — SP4 SQL backends should use a join.
        for account in await self.storage.accounts.list(
            provider=provider, status=AccountStatus.ACTIVE
        ):
            if await self.is_usable(account):
                usable.append(account)
        return usable

    async def pick(
        self,
        *,
        provider: str | None = None,
        requires_verified: Iterable[str] = (),
        exclude: Iterable[Any] = (),
        order: Order = "round_robin",
        key: Callable[[AccountT], Any] | None = None,
    ) -> AccountT | None:
        """Pick one usable account according to *order* (or a custom *key*).

        Args:
            provider: Filter candidates to this provider.
            requires_verified: Verification types that the account must have
                with status ``'verified'``.
            exclude: Additional account objects to exclude (beyond leased ones).
            order: Rotation strategy — ``'round_robin'``, ``'least_recently_used'``,
                or ``'random'``.  Ignored when *key* is provided.
            key: Custom key function; the account with the minimum key value is
                returned.

        Returns:
            A matching :class:`AccountT`, or ``None`` when no candidate exists.
        """
        excluded = {e.id for e in exclude} | self._leased
        candidates = [a for a in await self.list_usable(provider=provider) if a.id not in excluded]
        candidates = await self._filter_verified(candidates, requires_verified)
        if not candidates:
            return None
        # Stable secondary sort so deterministic order is consistent across runs.
        candidates.sort(key=lambda a: (a.created_at, str(a.id)))
        if key is not None:
            return min(candidates, key=key)
        if order == "random":
            return random.choice(candidates)
        if order == "least_recently_used":
            return await self._least_recently_used(candidates)
        # round_robin: modular in-process cursor — not identity-tracked, so a pool-size
        # change between calls may repeat a slot; acceptable for this reference backend.
        chosen = candidates[self._cursor % len(candidates)]
        self._cursor += 1
        return chosen

    async def _filter_verified(
        self, candidates: list[AccountT], requires_verified: Iterable[str]
    ) -> list[AccountT]:
        required = list(requires_verified)
        if not required:
            return candidates
        out: list[AccountT] = []
        # NOTE: one verifications query per (account, required type)
        # — SP4 SQL backends should use a join.
        for account in candidates:
            ok = True
            for vtype in required:
                current = await self.storage.verifications.current(account.id, vtype)
                if current is None or current.status != _VERIFIED:
                    ok = False
                    break
            if ok:
                out.append(account)
        return out

    async def _least_recently_used(self, candidates: list[AccountT]) -> AccountT:
        """Return the candidate with the oldest (or absent) last LOGIN_SUCCESS event."""
        scored: list[tuple[bool, datetime, AccountT]] = []
        for account in candidates:
            event = await self.storage.events.last(account.id, AccountEventType.LOGIN_SUCCESS)
            occurred = event.occurred_at if event is not None else None
            scored.append((occurred is not None, occurred or _MIN_TIME, account))
        scored.sort(key=lambda t: (t[0], t[1]))
        return scored[0][2]

    @asynccontextmanager
    async def lease(self, account: Any) -> AsyncIterator[Any]:
        """Async context manager that exclusively leases *account*.

        Raises :class:`~eazy_sdk_accounts.storage.exceptions.AccountLeasedError` if the
        account is already leased.  On exit the lease is released unconditionally.
        """
        if account.id in self._leased:
            raise AccountLeasedError("account already leased", account_id=account.id)
        self._leased.add(account.id)
        try:
            yield account
        finally:
            self._leased.discard(account.id)
