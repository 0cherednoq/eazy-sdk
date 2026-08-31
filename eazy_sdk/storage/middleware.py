"""Built-in storage middlewares: logging and event recording."""

from __future__ import annotations

import logging
from typing import Any

from eazy_sdk.logging import get_logger
from eazy_sdk.storage.entities import AccountEventType
from eazy_sdk.storage.repository import EventStore
from eazy_sdk.storage.storage import BaseStorageMiddleware, StorageOp

_logger = get_logger("storage")


class LoggingMiddleware(BaseStorageMiddleware):
    """Log each storage operation's redaction-safe summary (never secret values).

    Successful operations log at ``level`` (default DEBUG); failures log at WARNING.
    """

    def __init__(self, *, level: int = logging.DEBUG) -> None:
        self._level = level

    async def after(self, op: StorageOp) -> None:
        _logger.log(self._level, "storage op %s", op.name, extra={"eazy_sdk": op.redacted()})

    async def on_error(self, op: StorageOp, error: Exception) -> None:
        _logger.warning(
            "storage op %s failed",
            op.name,
            extra={"eazy_sdk": {**op.redacted(), "error": type(error).__name__}},
        )


_BAN = "ban"
_FREEZE = "freeze"
_VERIFIED = "verified"


def _event_type_for(op: StorageOp) -> str | None:
    """Map a canonical storage op to a canonical AccountEvent type, or None."""
    if op.name == "account.create":
        return AccountEventType.CREATED
    if op.name == "session.save":
        return AccountEventType.AUTHORIZED
    if op.name == "verification.set" and op.data.get("status") == _VERIFIED:
        return AccountEventType.VERIFIED
    if op.name == "restriction.add":
        rtype = op.data.get("type")
        if rtype == _BAN:
            return AccountEventType.BANNED
        if rtype == _FREEZE:
            return AccountEventType.FROZEN
    return None


class EventRecordingMiddleware(BaseStorageMiddleware):
    """Append a canonical :class:`AccountEvent` to the event store for lifecycle ops.

    Appends directly to the store (not through ``operation()``), so it does not
    recurse. Ops with no canonical mapping, or no ``account_id``, are ignored.
    """

    def __init__(self, events: EventStore[Any, Any]) -> None:
        self._events = events

    async def after(self, op: StorageOp) -> None:
        event_type = _event_type_for(op)
        if event_type is None or op.account_id is None:
            return
        await self._events.create(
            {"account_id": op.account_id, "type": event_type, "data": dict(op.data)}
        )
