"""In-memory reference backend (no locking; single-process use)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from eazy_sdk_accounts.storage.entities import (
    AccountEventRecord,
    AccountLinkRecord,
    AccountRecord,
    RestrictionRecord,
    SessionRecord,
    VerificationRecord,
    _utcnow,
)
from eazy_sdk_accounts.storage.exceptions import DuplicateAccountError
from eazy_sdk_accounts.storage.observers import EventRecordingObserver, LoggingObserver
from eazy_sdk_accounts.storage.storage import Storage, StorageObserver


def _apply(obj: Any, data: Mapping[str, Any]) -> None:
    for key, value in data.items():
        setattr(obj, key, value)
    if hasattr(obj, "updated_at"):
        obj.updated_at = _utcnow()


class InMemoryAccountRepository:
    """Dict-backed AccountRepository[AccountRecord, UUID]."""

    def __init__(self) -> None:
        self._data: dict[UUID, AccountRecord] = {}

    async def get(self, id: UUID) -> AccountRecord | None:
        return self._data.get(id)

    async def get_by_identifier(
        self, identifier: str, *, provider: str | None = None
    ) -> AccountRecord | None:
        for acc in self._data.values():
            if acc.identifier == identifier and acc.provider == provider:
                return acc
        return None

    async def create(self, data: Mapping[str, Any]) -> AccountRecord:
        acc = AccountRecord(**data)
        if await self.get_by_identifier(acc.identifier, provider=acc.provider) is not None:
            raise DuplicateAccountError(
                "account already exists",
                op="account.create",
                identifier=acc.identifier,
                provider=acc.provider,
            )
        self._data[acc.id] = acc
        return acc

    async def update(self, obj: AccountRecord, data: Mapping[str, Any]) -> AccountRecord:
        _apply(obj, data)
        return obj

    async def delete(self, obj: AccountRecord) -> None:
        self._data.pop(obj.id, None)

    async def list(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AccountRecord]:
        items = [
            acc
            for acc in self._data.values()
            if (provider is None or acc.provider == provider)
            and (status is None or acc.status == status)
        ]
        items.sort(key=lambda a: a.created_at)
        return items[offset : None if limit is None else offset + limit]


class InMemorySessionRepository:
    """Dict-backed SessionRepository[SessionRecord, UUID]."""

    def __init__(self) -> None:
        self._data: dict[UUID, SessionRecord] = {}

    async def get(self, id: UUID) -> SessionRecord | None:
        return self._data.get(id)

    async def create(self, data: Mapping[str, Any]) -> SessionRecord:
        session = SessionRecord(**data)
        self._data[session.id] = session
        return session

    async def update(self, obj: SessionRecord, data: Mapping[str, Any]) -> SessionRecord:
        _apply(obj, data)
        return obj

    async def delete(self, obj: SessionRecord) -> None:
        self._data.pop(obj.id, None)

    async def list_for_account(
        self, account_id: UUID, *, active: bool | None = None, label: str | None = None
    ) -> list[SessionRecord]:
        items = [
            s
            for s in self._data.values()
            if s.account_id == account_id
            and (active is None or s.is_active == active)
            and (label is None or s.label == label)
        ]
        items.sort(key=lambda s: s.created_at)
        return items

    async def get_active(
        self, account_id: UUID, *, label: str | None = None
    ) -> SessionRecord | None:
        active = await self.list_for_account(account_id, active=True, label=label)
        return active[-1] if active else None


class InMemoryVerificationRepository:
    """Dict-backed VerificationRepository[VerificationRecord, UUID]."""

    def __init__(self) -> None:
        self._data: dict[UUID, VerificationRecord] = {}

    async def get(self, id: UUID) -> VerificationRecord | None:
        return self._data.get(id)

    async def create(self, data: Mapping[str, Any]) -> VerificationRecord:
        v = VerificationRecord(**data)
        self._data[v.id] = v
        return v

    async def update(self, obj: VerificationRecord, data: Mapping[str, Any]) -> VerificationRecord:
        _apply(obj, data)
        return obj

    async def delete(self, obj: VerificationRecord) -> None:
        self._data.pop(obj.id, None)

    async def list_for_account(
        self, account_id: UUID, *, type: str | None = None
    ) -> list[VerificationRecord]:
        items = [
            v
            for v in self._data.values()
            if v.account_id == account_id and (type is None or v.type == type)
        ]
        items.sort(key=lambda v: v.created_at)
        return items

    async def current(self, account_id: UUID, type: str) -> VerificationRecord | None:
        items = await self.list_for_account(account_id, type=type)
        return items[-1] if items else None


class InMemoryRestrictionRepository:
    """Dict-backed RestrictionRepository[RestrictionRecord, UUID]."""

    def __init__(self) -> None:
        self._data: dict[UUID, RestrictionRecord] = {}

    async def get(self, id: UUID) -> RestrictionRecord | None:
        return self._data.get(id)

    async def create(self, data: Mapping[str, Any]) -> RestrictionRecord:
        r = RestrictionRecord(**data)
        self._data[r.id] = r
        return r

    async def update(self, obj: RestrictionRecord, data: Mapping[str, Any]) -> RestrictionRecord:
        _apply(obj, data)
        return obj

    async def delete(self, obj: RestrictionRecord) -> None:
        self._data.pop(obj.id, None)

    async def list_for_account(
        self, account_id: UUID, *, type: str | None = None, status: str | None = "active"
    ) -> list[RestrictionRecord]:
        items = [
            r
            for r in self._data.values()
            if r.account_id == account_id
            and (type is None or r.type == type)
            and (status is None or r.status == status)
        ]
        items.sort(key=lambda r: r.created_at)
        return items


class InMemoryLinkRepository:
    """Dict-backed LinkRepository[AccountLinkRecord, UUID]."""

    def __init__(self) -> None:
        self._data: dict[UUID, AccountLinkRecord] = {}

    async def get(self, id: UUID) -> AccountLinkRecord | None:
        return self._data.get(id)

    async def create(self, data: Mapping[str, Any]) -> AccountLinkRecord:
        link = AccountLinkRecord(**data)
        self._data[link.id] = link
        return link

    async def update(self, obj: AccountLinkRecord, data: Mapping[str, Any]) -> AccountLinkRecord:
        _apply(obj, data)
        return obj

    async def delete(self, obj: AccountLinkRecord) -> None:
        self._data.pop(obj.id, None)

    async def list_for_account(
        self, account_id: UUID, *, type: str | None = None
    ) -> list[AccountLinkRecord]:
        items = [
            link
            for link in self._data.values()
            if link.account_id == account_id and (type is None or link.type == type)
        ]
        items.sort(key=lambda link: link.created_at)
        return items


class InMemoryEventStore:
    """List-backed EventStore[AccountEventRecord, UUID] (append-only)."""

    def __init__(self) -> None:
        self._events: list[AccountEventRecord] = []

    async def create(self, data: Mapping[str, Any]) -> AccountEventRecord:
        event = AccountEventRecord(**data)
        return await self.append(event)

    async def append(self, event: AccountEventRecord) -> AccountEventRecord:
        self._events.append(event)
        return event

    async def list_for_account(
        self,
        account_id: UUID,
        *,
        type: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        order: Literal["asc", "desc"] = "desc",
    ) -> list[AccountEventRecord]:
        items = [
            e
            for e in self._events
            if e.account_id == account_id
            and (type is None or e.type == type)
            and (since is None or e.occurred_at >= since)
        ]
        items.sort(key=lambda e: e.occurred_at, reverse=(order == "desc"))
        return items if limit is None else items[:limit]

    async def last(self, account_id: UUID, type: str) -> AccountEventRecord | None:
        items = await self.list_for_account(account_id, type=type, order="desc")
        return items[0] if items else None

    async def count(self, account_id: UUID, type: str, *, since: datetime | None = None) -> int:
        return len(await self.list_for_account(account_id, type=type, since=since))


def MemoryStorage(
    *,
    observers: Sequence[StorageObserver] = (),
    record_events: bool = False,
    log_ops: bool = False,
) -> Storage[
    AccountRecord,
    SessionRecord,
    VerificationRecord,
    RestrictionRecord,
    AccountLinkRecord,
    AccountEventRecord,
    UUID,
]:
    """Build a fully-wired in-memory Storage (no locking; single-process).

    ``record_events=True`` appends an :class:`EventRecordingObserver` bound to the
    same event store; ``log_ops=True`` appends a :class:`LoggingObserver`.
    """
    events = InMemoryEventStore()
    chain: list[StorageObserver] = list(observers)
    if record_events:
        chain.append(EventRecordingObserver(events))
    if log_ops:
        chain.append(LoggingObserver())
    return Storage(
        accounts=InMemoryAccountRepository(),
        sessions=InMemorySessionRepository(),
        verifications=InMemoryVerificationRepository(),
        restrictions=InMemoryRestrictionRepository(),
        links=InMemoryLinkRepository(),
        events=events,
        observers=tuple(chain),
    )
