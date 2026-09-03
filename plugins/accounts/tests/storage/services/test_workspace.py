import eazy_sdk_accounts.storage as storage_pkg
from eazy_sdk_accounts.storage.memory import MemoryStorage
from eazy_sdk_accounts.storage.services.accounts import Accounts
from eazy_sdk_accounts.storage.services.pool import AccountPool
from eazy_sdk_accounts.storage.services.workspace import AccountWorkspace


def test_workspace_exposes_all_services() -> None:
    ws = AccountWorkspace(MemoryStorage())
    assert isinstance(ws.accounts, Accounts)
    assert isinstance(ws.pool, AccountPool)
    for name in ("sessions", "verifications", "restrictions", "links", "history"):
        assert getattr(ws, name) is not None


async def test_workspace_end_to_end_flow() -> None:
    ws = AccountWorkspace(MemoryStorage())

    gmail = await ws.accounts.create("bob@gmail.com", provider="gmail")
    acc = await ws.accounts.create("bob123", provider="example.com")
    await ws.links.add(acc, to=gmail, type="registered_via")
    await ws.verifications.mark(acc, "email", target="bob@gmail.com")

    from eazy_sdk_accounts.storage.entities import SessionData

    await ws.sessions.save(acc, SessionData(cookies={"sid": "x"}), label="proxy-7")
    await ws.history.record(acc, "login.success")

    target = await ws.pool.pick(provider="example.com", requires_verified={"email"})
    assert target is not None and target.identifier == "bob123"

    await ws.restrictions.ban(acc, reason="captcha")
    assert (await ws.pool.pick(provider="example.com")) is None
    assert (await ws.history.last_login(acc)) is not None
    assert [a.identifier for a in await ws.links.of(acc, type="registered_via")] == [
        "bob@gmail.com"
    ]


def test_public_surface_exports_services() -> None:
    for name in (
        "AccountWorkspace",
        "Accounts",
        "Sessions",
        "Verifications",
        "Restrictions",
        "AccountLinks",
        "History",
        "AccountPool",
    ):
        assert name in storage_pkg.__all__
        assert hasattr(storage_pkg, name)
