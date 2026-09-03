from uuid import uuid4

from eazy_sdk_accounts.storage.memory import InMemoryLinkRepository
from eazy_sdk_accounts.storage.repository import LinkRepository


async def test_links_list_for_account_and_type_filter() -> None:
    repo = InMemoryLinkRepository()
    assert isinstance(repo, LinkRepository)
    acc = uuid4()
    gmail = uuid4()
    tg = uuid4()

    await repo.create({"account_id": acc, "linked_account_id": gmail, "type": "registered_via"})
    await repo.create({"account_id": acc, "linked_account_id": tg, "type": "recovery"})
    await repo.create({"account_id": uuid4(), "linked_account_id": gmail, "type": "registered_via"})

    assert {link.linked_account_id for link in await repo.list_for_account(acc)} == {gmail, tg}
    via = await repo.list_for_account(acc, type="registered_via")
    assert [link.linked_account_id for link in via] == [gmail]
