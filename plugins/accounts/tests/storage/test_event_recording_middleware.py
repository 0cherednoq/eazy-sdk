from uuid import uuid4

from eazy_sdk_accounts.storage.entities import AccountEventType
from eazy_sdk_accounts.storage.memory import InMemoryEventStore
from eazy_sdk_accounts.storage.observers import EventRecordingObserver
from eazy_sdk_accounts.storage.storage import StorageObserver, StorageOp


def test_is_a_storage_middleware() -> None:
    assert isinstance(EventRecordingObserver(InMemoryEventStore()), StorageObserver)


async def test_maps_canonical_ops_to_events() -> None:
    events = InMemoryEventStore()
    mw = EventRecordingObserver(events)
    acc = uuid4()

    await mw.after(StorageOp("account.create", account_id=acc, data={"identifier": "bob"}))
    await mw.after(StorageOp("session.save", account_id=acc, data={"label": "p1"}))
    await mw.after(
        StorageOp("verification.set", account_id=acc, data={"type": "email", "status": "verified"})
    )
    await mw.after(StorageOp("restriction.add", account_id=acc, data={"type": "ban"}))
    await mw.after(StorageOp("restriction.add", account_id=acc, data={"type": "freeze"}))

    recorded = [e.type for e in await events.list_for_account(acc, order="asc")]
    assert recorded == [
        AccountEventType.CREATED,
        AccountEventType.AUTHORIZED,
        AccountEventType.VERIFIED,
        AccountEventType.BANNED,
        AccountEventType.FROZEN,
    ]


async def test_ignores_unmapped_and_unverified_and_missing_account() -> None:
    events = InMemoryEventStore()
    mw = EventRecordingObserver(events)
    acc = uuid4()

    await mw.after(StorageOp("session.invalidate", account_id=acc, data={"label": "a"}))  # unmapped
    # status "pending" — not verified, must not record
    await mw.after(
        StorageOp("verification.set", account_id=acc, data={"type": "sms", "status": "pending"})
    )
    await mw.after(StorageOp("account.create", account_id=None, data={}))  # no account id

    assert await events.list_for_account(acc) == []
