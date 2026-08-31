from datetime import UTC, datetime, timedelta

from eazy_sdk.storage.entities import AccountRecord
from eazy_sdk.storage.memory import MemoryStorage
from eazy_sdk.storage.services.accounts import Accounts
from eazy_sdk.storage.services.restrictions import Restrictions
from eazy_sdk.storage.storage import Storage


async def _account(storage: Storage) -> AccountRecord:
    return await Accounts(storage).create("bob")


async def test_ban_freeze_and_predicates() -> None:
    storage = MemoryStorage()
    r = Restrictions(storage)
    acc = await _account(storage)

    assert (await r.is_banned(acc)) is False
    await r.ban(acc, reason="captcha")
    assert (await r.is_banned(acc)) is True
    assert (await r.is_frozen(acc)) is False

    await r.freeze(acc)
    assert (await r.is_frozen(acc)) is True
    assert {x.type for x in await r.active(acc)} == {"ban", "freeze"}


async def test_lift_and_expiry() -> None:
    storage = MemoryStorage()
    r = Restrictions(storage)
    acc = await _account(storage)

    await r.ban(acc, reason="x")
    await r.lift(acc, type="ban")
    assert (await r.is_banned(acc)) is False

    past = datetime.now(UTC) - timedelta(hours=1)
    await r.restrict(acc, "limit", until=past)
    assert [x.type for x in await r.active(acc)] == []
