from eazy_sdk_accounts.storage.memory import MemoryStorage
from eazy_sdk_accounts.storage.services.accounts import Accounts
from eazy_sdk_accounts.storage.services.links import AccountLinks


async def test_add_of_and_remove() -> None:
    storage = MemoryStorage()
    accounts = Accounts(storage)
    links = AccountLinks(storage)

    site = await accounts.create("bob123", provider="example.com")
    gmail = await accounts.create("bob@gmail.com", provider="gmail")
    tg = await accounts.create("bob_tg", provider="telegram")

    await links.add(site, to=gmail, type="registered_via")
    await links.add(site, to=tg, type="recovery")

    all_linked = {a.identifier for a in await links.of(site)}
    assert all_linked == {"bob@gmail.com", "bob_tg"}
    via = await links.of(site, type="registered_via")
    assert [a.identifier for a in via] == ["bob@gmail.com"]

    await links.remove(site, to=gmail)
    assert {a.identifier for a in await links.of(site)} == {"bob_tg"}
