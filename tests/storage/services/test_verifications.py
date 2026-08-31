from eazy_sdk.storage.entities import AccountRecord
from eazy_sdk.storage.memory import MemoryStorage
from eazy_sdk.storage.services.accounts import Accounts
from eazy_sdk.storage.services.verifications import Verifications
from eazy_sdk.storage.storage import Storage


async def _account(storage: Storage) -> AccountRecord:
    return await Accounts(storage).create("bob")


async def test_mark_and_is_verified() -> None:
    storage = MemoryStorage()
    v = Verifications(storage)
    acc = await _account(storage)

    assert (await v.is_verified(acc, "email")) is False
    rec = await v.mark(acc, "email", target="bob@example.com")
    assert rec.status == "verified"
    assert rec.target == "bob@example.com"
    assert rec.verified_at is not None
    assert (await v.is_verified(acc, "email")) is True


async def test_set_status_and_pending() -> None:
    storage = MemoryStorage()
    v = Verifications(storage)
    acc = await _account(storage)

    await v.set(acc, "kyc", "pending")
    await v.set(acc, "sms", "pending")
    await v.mark(acc, "sms")
    pending_types = {x.type for x in await v.pending(acc)}
    assert pending_types == {"kyc"}
    assert (await v.is_verified(acc, "sms")) is True
