from eazy_sdk.storage.entities import AccountEventType, AccountRecord
from eazy_sdk.storage.memory import MemoryStorage
from eazy_sdk.storage.services.accounts import Accounts
from eazy_sdk.storage.services.history import History
from eazy_sdk.storage.storage import Storage


async def _account(storage: Storage) -> AccountRecord:
    return await Accounts(storage).create("bob")


async def test_record_and_typical_questions() -> None:
    storage = MemoryStorage()
    history = History(storage)
    acc = await _account(storage)

    assert (await history.last_login(acc)) is None
    assert (await history.failed_login_count(acc)) == 0
    assert (await history.banned_at(acc)) is None

    await history.record(acc, AccountEventType.LOGIN_FAILED)
    await history.record(acc, AccountEventType.LOGIN_SUCCESS)
    await history.record(acc, AccountEventType.LOGIN_FAILED)
    await history.record(acc, AccountEventType.BANNED, reason="captcha")

    assert (await history.last_login(acc)) is not None
    assert (await history.failed_login_count(acc)) == 2
    assert (await history.banned_at(acc)) is not None


async def test_record_and_timeline_orders_desc() -> None:
    storage = MemoryStorage()
    history = History(storage)
    acc = await _account(storage)

    await history.record(acc, "quest.completed", quest_id=7)
    await history.record(acc, "reward.daily_claimed", amount=10)

    timeline = await history.timeline(acc)
    assert [e.type for e in timeline] == ["reward.daily_claimed", "quest.completed"]
    assert timeline[0].data == {"amount": 10}
