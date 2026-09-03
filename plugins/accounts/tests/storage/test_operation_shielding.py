import logging

import pytest
from eazy_sdk_accounts.storage.memory import MemoryStorage
from eazy_sdk_accounts.storage.storage import BaseStorageObserver, StorageOp


class _Boom(BaseStorageObserver):
    """A buggy middleware that raises in every hook."""

    async def before(self, op: StorageOp) -> None:
        raise RuntimeError("before boom")

    async def after(self, op: StorageOp) -> None:
        raise RuntimeError("after boom")

    async def on_error(self, op: StorageOp, error: Exception) -> None:
        raise RuntimeError("on_error boom")


class _Spy(BaseStorageObserver):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def before(self, op: StorageOp) -> None:
        self.calls.append("before")

    async def after(self, op: StorageOp) -> None:
        self.calls.append("after")

    async def on_error(self, op: StorageOp, error: Exception) -> None:
        self.calls.append("on_error")


async def test_buggy_before_after_do_not_break_the_body(caplog: pytest.LogCaptureFixture) -> None:
    storage = MemoryStorage(observers=[_Boom()])
    ran = False
    with caplog.at_level(logging.WARNING, logger="eazy_sdk_accounts.storage"):
        async with storage.operation(StorageOp(name="account.create")):
            ran = True
    assert ran is True  # body ran despite before() raising
    assert sum("before boom" in r.message or "after boom" in r.message for r in caplog.records) == 2


async def test_on_error_hook_failure_does_not_mask_body_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = MemoryStorage(observers=[_Boom()])
    raises_ctx = pytest.raises(ValueError, match="real failure")
    with caplog.at_level(logging.WARNING, logger="eazy_sdk_accounts.storage"), raises_ctx:
        async with storage.operation(StorageOp(name="session.save")):
            raise ValueError("real failure")
    assert any("on_error boom" in r.message for r in caplog.records)


async def test_well_behaved_middleware_still_ordered() -> None:
    spy = _Spy()
    storage = MemoryStorage(observers=[spy])
    async with storage.operation(StorageOp(name="x")):
        spy.calls.append("body")
    assert spy.calls == ["before", "body", "after"]


async def test_well_behaved_runs_after_buggy_in_chain() -> None:
    spy = _Spy()
    storage = MemoryStorage(observers=[_Boom(), spy])
    async with storage.operation(StorageOp(name="x")):
        pass
    # _Boom raises in before/after; _Spy (later in the chain) must still receive both hooks
    assert spy.calls == ["before", "after"]
