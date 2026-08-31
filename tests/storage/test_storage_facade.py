import pytest

from eazy_sdk.storage.memory import MemoryStorage
from eazy_sdk.storage.storage import BaseStorageMiddleware, StorageMiddleware, StorageOp


def test_storage_op_redacted_omits_values() -> None:
    op = StorageOp(
        name="account.create",
        account_id="acc-1",
        data={"identifier": "bob", "secret": "hunter2"},
    )
    red = op.redacted()
    assert red == {
        "op": "account.create",
        "account_id": "acc-1",
        "data_keys": ["identifier", "secret"],
    }
    assert "hunter2" not in repr(red)


def test_memory_storage_wires_all_repositories() -> None:
    storage = MemoryStorage()
    assert storage.accounts is not None
    assert storage.sessions is not None
    assert storage.verifications is not None
    assert storage.restrictions is not None
    assert storage.links is not None
    assert storage.events is not None
    assert storage.middlewares == ()


class _Spy(BaseStorageMiddleware):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def before(self, op: StorageOp) -> None:
        self.calls.append(f"before:{op.name}")

    async def after(self, op: StorageOp) -> None:
        self.calls.append(f"after:{op.name}")

    async def on_error(self, op: StorageOp, error: Exception) -> None:
        self.calls.append(f"error:{op.name}:{type(error).__name__}")


async def test_operation_runs_before_then_after() -> None:
    spy = _Spy()
    assert isinstance(spy, StorageMiddleware)
    storage = MemoryStorage(middlewares=[spy])

    async with storage.operation(StorageOp(name="account.create")):
        spy.calls.append("body")

    assert spy.calls == ["before:account.create", "body", "after:account.create"]


async def test_operation_fires_on_error_and_reraises() -> None:
    spy = _Spy()
    storage = MemoryStorage(middlewares=[spy])

    with pytest.raises(ValueError):
        async with storage.operation(StorageOp(name="session.save")):
            raise ValueError("boom")

    assert spy.calls == ["before:session.save", "error:session.save:ValueError"]
