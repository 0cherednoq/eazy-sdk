"""Storage facade and the taskiq-style storage-middleware mechanism."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from eazy_sdk_accounts.storage.entities import (
    AccountEventRecord,
    AccountLinkRecord,
    AccountRecord,
    RestrictionRecord,
    SessionRecord,
    VerificationRecord,
)
from eazy_sdk_accounts.storage.repository import (
    AccountRepository,
    EventStore,
    LinkRepository,
    RestrictionRepository,
    SessionRepository,
    VerificationRepository,
)

_logger = logging.getLogger("eazy_sdk_accounts.storage")


@dataclass(frozen=True)
class StorageOp:
    """Describes a storage operation passed through the middleware chain."""

    name: str
    account_id: Any | None = None
    entity: Any | None = None  # intentionally excluded from redacted(): may be large/sensitive
    data: Mapping[str, Any] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """Log-safe view: names/keys only, never values (which may hold secrets)."""
        if self.account_id is None:
            account_id: str | int | None = None
        elif isinstance(self.account_id, str | int):
            account_id = self.account_id
        else:
            account_id = str(self.account_id)
        return {
            "op": self.name,
            "account_id": account_id,
            "data_keys": sorted(self.data),
        }


@runtime_checkable
class StorageObserver(Protocol):
    async def before(self, op: StorageOp) -> None: ...
    async def after(self, op: StorageOp) -> None: ...
    async def on_error(self, op: StorageOp, error: Exception) -> None: ...


class BaseStorageObserver:
    """No-op base: override only the hooks you need."""

    async def before(self, op: StorageOp) -> None:
        return None

    async def after(self, op: StorageOp) -> None:
        return None

    async def on_error(self, op: StorageOp, error: Exception) -> None:
        return None


@dataclass
class Storage[
    AccountT = AccountRecord,
    SessionT = SessionRecord,
    VerificationT = VerificationRecord,
    RestrictionT = RestrictionRecord,
    LinkT = AccountLinkRecord,
    EventT = AccountEventRecord,
    ID = UUID,
]:
    """Bundles the repositories and owns the middleware chain."""

    accounts: AccountRepository[AccountT, ID]
    sessions: SessionRepository[SessionT, ID]
    verifications: VerificationRepository[VerificationT, ID]
    restrictions: RestrictionRepository[RestrictionT, ID]
    links: LinkRepository[LinkT, ID]
    events: EventStore[EventT, ID]
    observers: Sequence[StorageObserver] = ()

    @asynccontextmanager
    async def operation(self, op: StorageOp) -> AsyncIterator[None]:
        # Middleware hooks are OBSERVATIONAL: a hook that raises is logged and
        # swallowed, never aborting the body or masking the body's exception.
        for mw in self.observers:
            await self._safe_hook(mw, "before", mw.before(op), op)
        try:
            yield
        except Exception as exc:
            for mw in self.observers:
                await self._safe_hook(mw, "on_error", mw.on_error(op, exc), op)
            raise
        for mw in self.observers:
            await self._safe_hook(mw, "after", mw.after(op), op)

    @staticmethod
    async def _safe_hook(
        mw: StorageObserver, hook: str, coro: Awaitable[None], op: StorageOp
    ) -> None:
        try:
            await coro
        except Exception as err:  # observational hooks must never break the op
            _logger.warning(
                "storage middleware hook failed: %s",
                err,
                exc_info=True,
                extra={
                    "eazy_sdk": {
                        **op.redacted(),
                        "middleware": type(mw).__name__,
                        "hook": hook,
                    }
                },
            )
