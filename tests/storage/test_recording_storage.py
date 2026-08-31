"""Tests for MemoryStorage convenience flags (record_events, log_ops) and public surface."""

from __future__ import annotations

import eazy_sdk.storage as storage_pkg
from eazy_sdk.storage.entities import AccountEventType
from eazy_sdk.storage.memory import MemoryStorage
from eazy_sdk.storage.middleware import EventRecordingMiddleware, LoggingMiddleware
from eazy_sdk.storage.services.workspace import AccountWorkspace


def test_memory_storage_wires_builtin_middlewares() -> None:
    storage = MemoryStorage(record_events=True, log_ops=True)
    kinds = {type(mw) for mw in storage.middlewares}
    assert EventRecordingMiddleware in kinds
    assert LoggingMiddleware in kinds


async def test_record_events_auto_populates_history() -> None:
    ws = AccountWorkspace(MemoryStorage(record_events=True))

    acc = await ws.accounts.create("bob", provider="x")
    await ws.verifications.mark(acc, "email")
    await ws.restrictions.ban(acc, reason="captcha")

    types = {e.type for e in await ws.history.timeline(acc)}
    assert AccountEventType.CREATED in types
    assert AccountEventType.VERIFIED in types
    assert AccountEventType.BANNED in types


def test_public_surface_exports_sp3_names() -> None:
    for name in ("LoggingMiddleware", "EventRecordingMiddleware", "SessionData"):
        assert name in storage_pkg.__all__
        assert hasattr(storage_pkg, name)
