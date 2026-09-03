from eazy_sdk_accounts.storage.entities import AccountRecord, SessionData
from eazy_sdk_accounts.storage.memory import MemoryStorage
from eazy_sdk_accounts.storage.services.accounts import Accounts
from eazy_sdk_accounts.storage.services.sessions import Sessions
from eazy_sdk_accounts.storage.storage import Storage


async def _account(storage: Storage) -> AccountRecord:
    return await Accounts(storage).create("bob", provider="x")


async def test_save_upserts_by_label_and_active_returns_latest() -> None:
    storage = MemoryStorage()
    sessions = Sessions(storage)
    acc = await _account(storage)

    s1 = await sessions.save(acc, SessionData(cookies={"sid": "1"}), label="proxy-1")
    assert s1.cookies == {"sid": "1"}
    s2 = await sessions.save(acc, SessionData(cookies={"sid": "2"}), label="proxy-1")
    assert s2.id == s1.id
    assert s2.cookies == {"sid": "2"}
    assert len(await storage.sessions.list_for_account(acc.id)) == 1

    assert (await sessions.active(acc, label="proxy-1")) is s2


async def test_invalidate_and_clear() -> None:
    storage = MemoryStorage()
    sessions = Sessions(storage)
    acc = await _account(storage)

    await sessions.save(acc, SessionData(cookies={"sid": "1"}), label="a")
    await sessions.save(acc, SessionData(cookies={"sid": "2"}), label="b")

    await sessions.invalidate(acc, label="a")
    assert (await sessions.active(acc, label="a")) is None
    assert (await sessions.active(acc, label="b")) is not None

    await sessions.clear(acc)
    assert await storage.sessions.list_for_account(acc.id) == []


async def test_save_preserves_audience_subject() -> None:
    storage = MemoryStorage()
    sessions = Sessions(storage)
    acc = await _account(storage)
    saved = await sessions.save(acc, SessionData(scheme="oidc", subject="user:42", audience="aud"))
    assert saved.subject == "user:42"
    assert saved.audience == "aud"
