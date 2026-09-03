import pytest
from eazy_sdk_accounts.storage.exceptions import DuplicateAccountError
from eazy_sdk_accounts.storage.memory import InMemoryAccountRepository
from eazy_sdk_accounts.storage.repository import AccountRepository


async def test_create_get_and_get_by_identifier() -> None:
    repo = InMemoryAccountRepository()
    assert isinstance(repo, AccountRepository)  # runtime_checkable structural conformance

    acc = await repo.create({"identifier": "bob", "provider": "example.com"})
    assert acc.identifier == "bob"

    assert (await repo.get(acc.id)) is acc
    assert (await repo.get_by_identifier("bob", provider="example.com")) is acc
    assert (await repo.get_by_identifier("bob", provider="other")) is None
    assert (await repo.get_by_identifier("missing")) is None


async def test_duplicate_identifier_provider_rejected() -> None:
    repo = InMemoryAccountRepository()
    await repo.create({"identifier": "bob", "provider": "example.com"})
    with pytest.raises(DuplicateAccountError):
        await repo.create({"identifier": "bob", "provider": "example.com"})
    await repo.create({"identifier": "bob", "provider": "other"})


async def test_update_and_delete_and_list_filters() -> None:
    repo = InMemoryAccountRepository()
    a = await repo.create({"identifier": "a", "provider": "p1"})
    b = await repo.create({"identifier": "b", "provider": "p2"})
    await repo.update(b, {"status": "deleted"})

    assert {x.identifier for x in await repo.list()} == {"a", "b"}
    assert [x.identifier for x in await repo.list(provider="p1")] == ["a"]
    assert [x.identifier for x in await repo.list(status="deleted")] == ["b"]

    await repo.delete(a)
    assert (await repo.get(a.id)) is None
    assert [x.identifier for x in await repo.list()] == ["b"]
