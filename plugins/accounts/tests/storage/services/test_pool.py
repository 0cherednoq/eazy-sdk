import pytest
from eazy_sdk_accounts.storage.entities import AccountEventType
from eazy_sdk_accounts.storage.exceptions import AccountLeasedError
from eazy_sdk_accounts.storage.memory import MemoryStorage
from eazy_sdk_accounts.storage.services.accounts import Accounts
from eazy_sdk_accounts.storage.services.history import History
from eazy_sdk_accounts.storage.services.pool import AccountPool
from eazy_sdk_accounts.storage.services.restrictions import Restrictions
from eazy_sdk_accounts.storage.services.verifications import Verifications


async def test_is_usable_excludes_banned_and_inactive() -> None:
    storage = MemoryStorage()
    accounts, restrictions, pool = Accounts(storage), Restrictions(storage), AccountPool(storage)

    good = await accounts.create("good", provider="x")
    banned = await accounts.create("banned", provider="x")
    await restrictions.ban(banned)
    inactive = await accounts.create("inactive", provider="x", status="inactive")

    assert (await pool.is_usable(good)) is True
    assert (await pool.is_usable(banned)) is False
    assert (await pool.is_usable(inactive)) is False
    assert {a.identifier for a in await pool.list_usable(provider="x")} == {"good"}


async def test_pick_round_robin_rotates() -> None:
    storage = MemoryStorage()
    accounts, pool = Accounts(storage), AccountPool(storage)
    await accounts.create("a", provider="x")
    await accounts.create("b", provider="x")
    await accounts.create("c", provider="x")

    picks: list[str] = []
    for _ in range(4):
        chosen = await pool.pick(provider="x", order="round_robin")
        assert chosen is not None
        picks.append(chosen.identifier)
    assert picks == ["a", "b", "c", "a"]


async def test_pick_requires_verified_and_returns_none_when_empty() -> None:
    storage = MemoryStorage()
    accounts, verifs, pool = Accounts(storage), Verifications(storage), AccountPool(storage)
    a = await accounts.create("a", provider="x")
    await accounts.create("b", provider="x")
    await verifs.mark(a, "email")

    chosen = await pool.pick(provider="x", requires_verified={"email"})
    assert chosen is not None and chosen.identifier == "a"

    none = await pool.pick(provider="x", requires_verified={"kyc"})
    assert none is None


async def test_pick_least_recently_used() -> None:
    storage = MemoryStorage()
    accounts, history, pool = Accounts(storage), History(storage), AccountPool(storage)
    a = await accounts.create("a", provider="x")
    await accounts.create("b", provider="x")
    await history.record(a, AccountEventType.LOGIN_SUCCESS)

    chosen = await pool.pick(provider="x", order="least_recently_used")
    assert chosen is not None and chosen.identifier == "b"


async def test_lease_blocks_double_lease_and_pick_skips_leased() -> None:
    storage = MemoryStorage()
    accounts, pool = Accounts(storage), AccountPool(storage)
    a = await accounts.create("a", provider="x")

    async with pool.lease(a):
        assert (await pool.pick(provider="x")) is None
        with pytest.raises(AccountLeasedError):
            async with pool.lease(a):
                pass
    assert (await pool.pick(provider="x")) is not None
