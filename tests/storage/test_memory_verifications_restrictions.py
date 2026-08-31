from uuid import uuid4

from eazy_sdk.storage.memory import (
    InMemoryRestrictionRepository,
    InMemoryVerificationRepository,
)
from eazy_sdk.storage.repository import RestrictionRepository, VerificationRepository


async def test_verifications_current_and_list() -> None:
    repo = InMemoryVerificationRepository()
    assert isinstance(repo, VerificationRepository)
    account_id = uuid4()

    await repo.create({"account_id": account_id, "type": "email", "status": "pending"})
    v2 = await repo.create({"account_id": account_id, "type": "email", "status": "verified"})
    await repo.create({"account_id": account_id, "type": "sms", "status": "pending"})

    assert {v.type for v in await repo.list_for_account(account_id)} == {"email", "sms"}
    assert [v.type for v in await repo.list_for_account(account_id, type="sms")] == ["sms"]
    assert (await repo.current(account_id, "email")) is v2
    assert (await repo.current(account_id, "passport")) is None


async def test_restrictions_status_filter() -> None:
    repo = InMemoryRestrictionRepository()
    assert isinstance(repo, RestrictionRepository)
    account_id = uuid4()

    r1 = await repo.create({"account_id": account_id, "type": "ban", "status": "active"})
    await repo.create({"account_id": account_id, "type": "freeze", "status": "lifted"})

    # default status="active"
    assert [r.type for r in await repo.list_for_account(account_id)] == ["ban"]
    assert {r.type for r in await repo.list_for_account(account_id, status=None)} == {
        "ban",
        "freeze",
    }
    assert [r.type for r in await repo.list_for_account(account_id, type="ban")] == ["ban"]
    _ = r1
