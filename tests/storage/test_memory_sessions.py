from uuid import uuid4

from eazy_sdk.storage.memory import InMemorySessionRepository
from eazy_sdk.storage.repository import SessionRepository


async def test_session_crud_and_queries() -> None:
    repo = InMemorySessionRepository()
    assert isinstance(repo, SessionRepository)
    account_id = uuid4()

    s1 = await repo.create({"account_id": account_id, "label": "proxy-1", "cookies": {"sid": "1"}})
    s2 = await repo.create({"account_id": account_id, "label": "proxy-2", "is_active": False})
    await repo.create({"account_id": uuid4(), "label": "other"})

    for_acc = await repo.list_for_account(account_id)
    assert {s.label for s in for_acc} == {"proxy-1", "proxy-2"}
    assert [s.label for s in await repo.list_for_account(account_id, active=True)] == ["proxy-1"]
    assert [s.label for s in await repo.list_for_account(account_id, label="proxy-2")] == [
        "proxy-2"
    ]

    assert (await repo.get_active(account_id, label="proxy-1")) is s1
    assert (await repo.get_active(account_id, label="proxy-2")) is None  # s2 is inactive

    await repo.update(s2, {"is_active": True})
    assert (await repo.get_active(account_id, label="proxy-2")) is s2
    _ = s1
