# eazy-sdk-sqlmodel

Async five-table account storage for Eazy SDK:

- `accounts` — identity, arbitrary plaintext typed credentials, profile and CAS revision;
- `sessions` — one key/revision/opaque payload path for token, cookie and browser state;
- `verifications` — challenge history, resend chain and optional proof-delivery account;
- `account_links` — directed relations and database-enforced resource reservations;
- `account_events` — append-only lifecycle history.

Install:

```console
uv add eazy-sdk-sqlmodel
```

Quickstart:

```python
from sqlalchemy.ext.asyncio import create_async_engine
from eazy_sdk_sqlmodel import open_workspace

engine = create_async_engine("sqlite+aiosqlite:///accounts.db")

async with open_workspace(engine) as workspace:
    account = await workspace.accounts.create(
        "ada@example.test",
        provider="example.test",
        credentials={
            "format": "plain-json",
            "version": 1,
            "data": {"password": "correct-horse"},
        },
        profile={"first_name": "Ada"},
    )

await engine.dispose()
```

`open_workspace` borrows the engine, commits a successful block and rolls back an exceptional block.
The caller still owns and disposes the engine.

For registration, pass Pydantic model types; `SecretStr` and `SecretBytes` are persisted in full by
the default plaintext codec but remain redacted from model `repr` and codec errors:

```python
from eazy_sdk_sqlmodel import SqlRegistrationStore

store = SqlRegistrationStore(
    session,
    credentials_model=SignupCredentials,
    profile_model=AccountProfile,
    session_model=UserSession,
)
```

Use a dedicated `AsyncSession` for `SqlRegistrationStore`/`SqlSessionStore`. These lifecycle stores
commit each state+event boundary before returning and reject an explicit outer transaction; this is
what makes reservations visible to another process before remote I/O starts.

Production upgrades are explicit and one-shot:

```console
eazy-sdk-sqlmodel-migrate sqlite+aiosqlite:///accounts.db
eazy-sdk-sqlmodel-migrate sqlite+aiosqlite:///accounts.db --apply
```

The first command is read-only. The equivalent programmatic API is:

```python
from eazy_sdk_sqlmodel import inspect_v1, migrate_v1_to_v2

report = await inspect_v1(engine)
if report.can_apply:
    await migrate_v1_to_v2(engine)
```

The dry-run report stops on normalized duplicate identities/remote IDs, orphan references,
duplicate active session keys and legacy restrictions that require application policy. Successful
migration removes the v1 tables; the runtime has no dual-schema read/write path.
