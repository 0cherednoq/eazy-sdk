from datetime import UTC, datetime, timedelta
from uuid import uuid4

from eazy_sdk.storage.entities import AccountEventRecord
from eazy_sdk.storage.memory import InMemoryEventStore
from eazy_sdk.storage.repository import EventStore


async def test_append_list_ordering_last_count() -> None:
    store = InMemoryEventStore()
    assert isinstance(store, EventStore)
    acc = uuid4()
    base = datetime(2026, 1, 1, tzinfo=UTC)

    await store.append(AccountEventRecord(account_id=acc, type="login.failed", occurred_at=base))
    await store.append(
        AccountEventRecord(
            account_id=acc, type="login.success", occurred_at=base + timedelta(hours=1)
        )
    )
    await store.append(
        AccountEventRecord(
            account_id=acc, type="login.failed", occurred_at=base + timedelta(hours=2)
        )
    )
    await store.append(
        AccountEventRecord(account_id=uuid4(), type="login.success", occurred_at=base)
    )

    desc = await store.list_for_account(acc)
    assert [e.occurred_at for e in desc] == [
        base + timedelta(hours=2),
        base + timedelta(hours=1),
        base,
    ]
    asc = await store.list_for_account(acc, order="asc")
    assert [e.occurred_at for e in asc] == [
        base,
        base + timedelta(hours=1),
        base + timedelta(hours=2),
    ]

    assert [e.type for e in await store.list_for_account(acc, type="login.failed")] == [
        "login.failed",
        "login.failed",
    ]

    last_ok = await store.last(acc, "login.success")
    assert last_ok is not None and last_ok.occurred_at == base + timedelta(hours=1)
    assert (await store.last(acc, "never")) is None

    assert await store.count(acc, "login.failed") == 2
    assert await store.count(acc, "login.failed", since=base + timedelta(hours=1)) == 1


async def test_list_limit() -> None:
    store = InMemoryEventStore()
    acc = uuid4()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        await store.append(
            AccountEventRecord(account_id=acc, type="x", occurred_at=base + timedelta(hours=i))
        )
    top2 = await store.list_for_account(acc, limit=2)  # default order="desc" → most recent first
    assert [e.occurred_at for e in top2] == [base + timedelta(hours=4), base + timedelta(hours=3)]
