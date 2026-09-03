"""Repository protocols (fastapi-users-style async DB adapters)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable


@runtime_checkable
class Repository[T, ID](Protocol):
    async def get(self, id: ID) -> T | None: ...
    async def create(self, data: Mapping[str, Any]) -> T: ...
    async def update(self, obj: T, data: Mapping[str, Any]) -> T: ...
    async def delete(self, obj: T) -> None: ...


@runtime_checkable
class AccountRepository[AccountT, ID](Repository[AccountT, ID], Protocol):
    async def get_by_identifier(
        self, identifier: str, *, provider: str | None = None
    ) -> AccountT | None: ...
    async def list(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AccountT]: ...


@runtime_checkable
class SessionRepository[SessionT, ID](Repository[SessionT, ID], Protocol):
    async def list_for_account(
        self, account_id: ID, *, active: bool | None = None, label: str | None = None
    ) -> list[SessionT]: ...
    async def get_active(self, account_id: ID, *, label: str | None = None) -> SessionT | None: ...


@runtime_checkable
class VerificationRepository[VerificationT, ID](Repository[VerificationT, ID], Protocol):
    async def list_for_account(
        self, account_id: ID, *, type: str | None = None
    ) -> list[VerificationT]: ...
    async def current(self, account_id: ID, type: str) -> VerificationT | None: ...


@runtime_checkable
class RestrictionRepository[RestrictionT, ID](Repository[RestrictionT, ID], Protocol):
    async def list_for_account(
        self, account_id: ID, *, type: str | None = None, status: str | None = "active"
    ) -> list[RestrictionT]: ...


@runtime_checkable
class LinkRepository[LinkT, ID](Repository[LinkT, ID], Protocol):
    async def list_for_account(self, account_id: ID, *, type: str | None = None) -> list[LinkT]: ...


@runtime_checkable
class EventStore[EventT, ID](Protocol):
    async def create(self, data: Mapping[str, Any]) -> EventT: ...
    async def append(self, event: EventT) -> EventT: ...
    async def list_for_account(
        self,
        account_id: ID,
        *,
        type: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        order: Literal["asc", "desc"] = "desc",
    ) -> list[EventT]: ...
    async def last(self, account_id: ID, type: str) -> EventT | None: ...
    async def count(self, account_id: ID, type: str, *, since: datetime | None = None) -> int: ...
