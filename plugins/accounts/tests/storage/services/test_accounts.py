from eazy_sdk_accounts.storage.entities import Credentials
from eazy_sdk_accounts.storage.memory import MemoryStorage
from eazy_sdk_accounts.storage.services.accounts import Accounts
from eazy_sdk_accounts.storage.storage import BaseStorageObserver, StorageOp


class _Spy(BaseStorageObserver):
    def __init__(self) -> None:
        self.ops: list[StorageOp] = []

    async def after(self, op: StorageOp) -> None:
        self.ops.append(op)


async def test_create_get_list_and_emits_op() -> None:
    spy = _Spy()
    accounts = Accounts(MemoryStorage(observers=[spy]))

    acc = await accounts.create("bob", provider="example.com", credentials=Credentials())
    assert acc.identifier == "bob"
    assert acc.provider == "example.com"
    assert (await accounts.get(acc.id)) is acc
    assert (await accounts.get_by_identifier("bob", provider="example.com")) is acc
    assert [a.identifier for a in await accounts.list()] == ["bob"]

    assert [op.name for op in spy.ops] == ["account.create"]
    assert spy.ops[0].account_id == acc.id
    assert "credentials" not in spy.ops[0].data


async def test_get_or_create_is_idempotent() -> None:
    accounts = Accounts(MemoryStorage())
    a = await accounts.get_or_create("bob", provider="x")
    b = await accounts.get_or_create("bob", provider="x")
    assert a is b


async def test_update_set_status_and_soft_delete() -> None:
    accounts = Accounts(MemoryStorage())
    acc = await accounts.create("bob")
    await accounts.update(acc, provider="newhost")
    assert acc.provider == "newhost"
    await accounts.set_status(acc, "inactive")
    assert acc.status == "inactive"
    deleted = await accounts.delete(acc)
    assert deleted.status == "deleted"


async def test_update_does_not_leak_credentials_into_op() -> None:
    spy = _Spy()
    accounts = Accounts(MemoryStorage(observers=[spy]))
    acc = await accounts.create("bob")
    spy.ops.clear()  # drop the account.create op

    await accounts.update(acc, provider="newhost", credentials=Credentials())
    assert [op.name for op in spy.ops] == ["account.update"]
    assert "credentials" not in spy.ops[0].data
    assert spy.ops[0].data == {"provider": "newhost"}
    # the credentials WERE persisted on the record (only the op's logged data is filtered)
    assert acc.credentials is not None
