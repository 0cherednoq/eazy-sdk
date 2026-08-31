from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import httpx
import pytest
from eazy_sdk_sqlmodel import (
    DEFAULT_PROVIDER,
    PlainPydanticCodec,
    SqlConcurrencyError,
    SqlModelConfigurationError,
    SqlResourceConflictError,
    SqlSessionStore,
    SqlValueCodecError,
    build_sqlmodel_storage,
    create_all,
    open_workspace,
    schema_ddl,
)
from eazy_sdk_sqlmodel.tables import (
    TABLE_NAMES,
    Account,
    AccountEvent,
    AccountLink,
    Session,
    Verification,
)
from pydantic import BaseModel, ConfigDict, SecretBytes, SecretStr
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from eazy_sdk import AsyncApi, AsyncClient, ClientConfig, api
from eazy_sdk.accounts.session import SessionKey, SessionRevision, SessionRevisionError
from eazy_sdk.auth import AuthContext, Bearer, ExpiresAt, RefreshToken, session_auth
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import JsonBody
from eazy_sdk.response import Json, NormalizedResponse, Responses
from eazy_sdk.storage.exceptions import DuplicateAccountError


def client_from_httpx(raw: httpx.AsyncClient, *, config: ClientConfig) -> AsyncClient:
    return AsyncClient(
        base_url=str(raw.base_url),
        handler=AsyncHttpxHandler(raw, owns_client=True),
        config=config,
    )


class Nested(BaseModel):
    security_answers: dict[str, str]


class Credentials(BaseModel):
    username: str
    password: SecretStr
    recovery: SecretBytes
    nested: Nested
    devices: list[str]


class Unsupported(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    value: object


class RichValue(BaseModel):
    identifier: UUID
    issued_at: datetime
    nested: list[dict[str, str]]


class TokenSession(BaseModel):
    access_token: SecretStr
    expires_at: datetime


class OpaqueSession(BaseModel):
    state: dict[str, object]


class SqlLoginCredentials(BaseModel):
    username: str
    password: SecretStr


class SqlLoginRequest(BaseModel):
    username: str
    password: str


class SqlRefreshRequest(BaseModel):
    refresh_token: str


class SqlAuthSession(BaseModel):
    access_token: Annotated[SecretStr, Bearer()]
    refresh_token: Annotated[SecretStr, RefreshToken()]
    expires_at: Annotated[datetime, ExpiresAt()]


class SqlSessionApi(AsyncApi):
    @api.post(
        "/login",
        operation_id="sqlLogin",
        responses=Responses(success={200: Json(SqlAuthSession)}),
    )
    async def login(self, *, body: Annotated[SqlLoginRequest, JsonBody()]) -> SqlAuthSession:
        raise NotImplementedError

    @api.post(
        "/refresh",
        operation_id="sqlRefresh",
        responses=Responses(success={200: Json(SqlAuthSession)}),
    )
    async def refresh(self, *, body: Annotated[SqlRefreshRequest, JsonBody()]) -> SqlAuthSession:
        raise NotImplementedError


class SqlSdk:
    def __init__(self, client: Any) -> None:
        self.auth = SqlSessionApi(client)


class SqlAuthService:
    async def acquire(
        self,
        credentials: SqlLoginCredentials,
        context: AuthContext[SqlSdk],
    ) -> SqlAuthSession:
        return await context.sdk.auth.login(
            body=SqlLoginRequest(
                username=credentials.username,
                password=credentials.password.get_secret_value(),
            )
        )

    async def refresh(
        self,
        current: SqlAuthSession,
        context: AuthContext[SqlSdk],
    ) -> SqlAuthSession:
        return await context.sdk.auth.refresh(
            body=SqlRefreshRequest(refresh_token=current.refresh_token.get_secret_value())
        )


async def test_default_metadata_has_exact_five_tables_and_foreign_keys(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    assert names == TABLE_NAMES
    assert "eazy_sdk_restrictions" not in names

    expected = {
        "sessions": {"account_id"},
        "verifications": {"account_id", "via_account_id", "replaces_id"},
        "account_links": {"owner_account_id", "resource_account_id"},
        "account_events": {"account_id", "session_id", "verification_id", "link_id"},
    }
    for table, columns in expected.items():

        def foreign_key_columns(sync, *, table_name: str = table) -> set[str]:  # type: ignore[no-untyped-def]
            return {
                column
                for foreign_key in inspect(sync).get_foreign_keys(table_name)
                for column in foreign_key["constrained_columns"]
            }

        keys = await connection.run_sync(foreign_key_columns)
        assert keys == columns


def test_plain_codec_roundtrip_and_redaction() -> None:
    value = Credentials(
        username="ada",
        password=SecretStr("correct-horse"),
        recovery=SecretBytes(b"\x00\xffsecret"),
        nested=Nested(security_answers={"pet": "Toby"}),
        devices=["device-42"],
    )
    codec = PlainPydanticCodec(Credentials)
    encoded = codec.encode(value)
    assert encoded["data"]["password"] == "correct-horse"  # type: ignore[index]
    assert codec.decode(encoded) == value
    assert "correct-horse" not in repr(codec)

    marker = object()
    with pytest.raises(SqlValueCodecError) as captured:
        PlainPydanticCodec(Unsupported).encode(Unsupported(value=marker))
    assert hex(id(marker)) not in str(captured.value)

    rich = RichValue(
        identifier=uuid4(),
        issued_at=datetime(2026, 8, 15, 12, tzinfo=UTC),
        nested=[{"kind": "device"}],
    )
    assert PlainPydanticCodec(RichValue).decode(PlainPydanticCodec(RichValue).encode(rich)) == rich


async def test_account_normalization_uniqueness_and_database_cas(session: AsyncSession) -> None:
    storage = build_sqlmodel_storage(session)
    first = await storage.accounts.create(
        {
            "identifier": " ada ",
            "provider": None,
            "status": "provisioning",
            "credentials": {},
            "profile": {},
        }
    )
    assert first.identifier == "ada"
    assert first.provider == DEFAULT_PROVIDER
    await session.commit()
    with pytest.raises(DuplicateAccountError):
        await storage.accounts.create({"identifier": "ada", "provider": None})
    await session.rollback()

    row = await storage.accounts.get_by_identifier("ada")
    assert row is not None
    updated = await storage.accounts.compare_and_swap(row.id, 0, {"status": "active"})
    assert updated.revision == 1
    with pytest.raises(SqlConcurrencyError):
        await storage.accounts.compare_and_swap(row.id, 0, {"status": "inactive"})

    await storage.accounts.create(
        {"identifier": "remote-owner", "provider": "site", "remote_id": "remote-1"}
    )
    await session.commit()
    with pytest.raises(DuplicateAccountError):
        await storage.accounts.create(
            {"identifier": "other-owner", "provider": "site", "remote_id": "remote-1"}
        )


async def test_profile_and_account_repr_do_not_expose_credentials(session: AsyncSession) -> None:
    encoded = PlainPydanticCodec(Credentials).encode(
        Credentials(
            username="ada",
            password=SecretStr("correct-horse"),
            recovery=SecretBytes(b"secret"),
            nested=Nested(security_answers={}),
            devices=[],
        )
    )
    account = Account(identifier="ada", credentials=encoded, profile={"name": "Ada"})
    session.add(account)
    await session.flush()
    assert "correct-horse" not in repr(account)
    assert account.profile == {"name": "Ada"}


async def test_link_reservation_release_reactivation_and_events(session: AsyncSession) -> None:
    owner1 = Account(identifier="owner-1")
    owner2 = Account(identifier="owner-2")
    resource = Account(provider="mail", identifier="ada@example.test")
    session.add_all([owner1, owner2, resource])
    await session.flush()
    owner1_id, owner2_id, resource_id = owner1.id, owner2.id, resource.id
    storage = build_sqlmodel_storage(session)
    first = await storage.links.reserve(
        owner_account_id=owner1_id,
        resource_account_id=resource_id,
        relation="registration_identity",
        exclusive_scope="registration",
    )
    await session.commit()
    first_id = first.id

    with pytest.raises(SqlResourceConflictError):
        async with session.begin():
            await storage.links.reserve(
                owner_account_id=owner2_id,
                resource_account_id=resource_id,
                relation="registration_identity",
                exclusive_scope="registration",
            )

    stored = await session.get(AccountLink, first_id)
    assert stored is not None
    await storage.links.release(stored)
    await session.flush()
    second = await storage.links.reserve(
        owner_account_id=owner2_id,
        resource_account_id=resource_id,
        relation="registration_identity",
        exclusive_scope="registration",
    )
    await storage.links.release(second)
    await session.flush()
    reactivated = await storage.links.reserve(
        owner_account_id=owner2_id,
        resource_account_id=resource_id,
        relation="registration_identity",
        exclusive_scope="registration:site",
    )
    assert reactivated.id == second.id


async def test_same_resource_can_use_different_exclusive_scopes(session: AsyncSession) -> None:
    owner1 = Account(identifier="scope-owner-1")
    owner2 = Account(identifier="scope-owner-2")
    resource = Account(identifier="scope-resource")
    session.add_all([owner1, owner2, resource])
    await session.flush()
    links = build_sqlmodel_storage(session).links
    first = await links.reserve(
        owner_account_id=owner1.id,
        resource_account_id=resource.id,
        relation="registration_identity",
        exclusive_scope="registration:site-a",
    )
    second = await links.reserve(
        owner_account_id=owner2.id,
        resource_account_id=resource.id,
        relation="registration_identity",
        exclusive_scope="registration:site-b",
    )
    assert first.id != second.id


async def test_nonexclusive_links_and_database_constraints(session: AsyncSession) -> None:
    owner1 = Account(identifier="owner-a")
    owner2 = Account(identifier="owner-b")
    resource = Account(identifier="shared")
    session.add_all([owner1, owner2, resource])
    await session.flush()
    storage = build_sqlmodel_storage(session)
    await storage.links.reserve(
        owner_account_id=owner1.id,
        resource_account_id=resource.id,
        relation="recovery",
        exclusive_scope=None,
    )
    await storage.links.reserve(
        owner_account_id=owner2.id,
        resource_account_id=resource.id,
        relation="recovery",
        exclusive_scope=None,
    )
    with pytest.raises(IntegrityError):
        session.add(
            AccountLink(
                owner_account_id=owner1.id,
                resource_account_id=owner1.id,
                relation="self",
            )
        )
        await session.flush()


async def test_released_link_still_blocks_resource_hard_delete_until_explicit_purge(
    session: AsyncSession,
) -> None:
    owner = Account(identifier="purge-owner")
    resource = Account(identifier="purge-resource")
    session.add_all([owner, resource])
    await session.flush()
    storage = build_sqlmodel_storage(session)
    link = await storage.links.reserve(
        owner_account_id=owner.id,
        resource_account_id=resource.id,
        relation="registration_identity",
        exclusive_scope="registration",
    )
    await storage.links.release(link)
    session.add(
        Session(
            account_id=resource.id,
            key="browser",
            kind="browser",
            payload={"format": "plain-json", "version": 1, "data": {}},
        )
    )
    await session.commit()
    resource_id, link_id = resource.id, link.id

    stored_resource = await session.get(Account, resource_id)
    assert stored_resource is not None
    await session.delete(stored_resource)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    stored_link = await session.get(AccountLink, link_id)
    assert stored_link is not None
    await session.delete(stored_link)
    stored_resource = await session.get(Account, resource_id)
    assert stored_resource is not None
    await session.delete(stored_resource)
    await session.commit()
    assert await session.get(Account, resource_id) is None
    assert (
        not (await session.execute(select(Session).where(Session.account_id == resource_id)))
        .scalars()
        .all()
    )


async def test_session_store_one_payload_path_and_cas(session: AsyncSession) -> None:
    account = Account(identifier="session-owner")
    session.add(account)
    await session.flush()
    store = SqlSessionStore(
        session,
        account.id,
        session_model=TokenSession,
        kind="bearer",
    )
    key = SessionKey("primary")
    first_value = TokenSession(
        access_token=SecretStr("access-v1"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    first = SessionRevision(1)
    await store.save(key, first_value, first)
    loaded = await store.load(key)
    assert loaded is not None and loaded.value == first_value
    second = SessionRevision(2)
    await store.save(
        key,
        first_value.model_copy(update={"access_token": SecretStr("access-v2")}),
        second,
    )
    with pytest.raises(SessionRevisionError):
        await store.save(key, first_value, second)
    secondary = SessionKey("secondary")
    await store.save(secondary, first_value, SessionRevision(1))
    assert (await store.load(secondary)) is not None
    await store.invalidate(key, first)
    assert await store.load(key) is not None
    await store.invalidate(key, second)
    assert await store.load(key) is None
    rows = (await session.execute(select(Session))).scalars().all()
    primary = next(row for row in rows if row.key == "primary")
    assert len(rows) == 2 and primary.revision == 3
    assert "access-v2" not in repr(primary)
    event_types = {
        event.type for event in (await session.execute(select(AccountEvent))).scalars().all()
    }
    assert event_types == {"session.created", "session.refreshed", "session.invalidated"}


async def test_session_auth_login_and_refresh_persist_through_sql_store(
    session: AsyncSession,
) -> None:
    account = Account(identifier="session-auth-owner")
    session.add(account)
    await session.commit()
    store = SqlSessionStore(
        session,
        account.id,
        session_model=SqlAuthSession,
        kind="bearer",
    )
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        authorization = request.headers.get("Authorization", "")
        calls.append((request.url.path, authorization))
        if request.url.path == "/login":
            return httpx.Response(
                200,
                json={
                    "access_token": "access-v1",
                    "refresh_token": "refresh-v1",
                    "expires_at": "2030-01-01T01:00:00Z",
                },
            )
        if request.url.path == "/refresh":
            return httpx.Response(
                200,
                json={
                    "access_token": "access-v2",
                    "refresh_token": "refresh-v2",
                    "expires_at": "2030-01-01T01:00:00Z",
                },
            )
        if authorization == "Bearer access-v1":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"authorization": authorization})

    auth = session_auth(
        SqlAuthSession,
        credentials=SqlLoginCredentials(username="ada", password=SecretStr("secret")),
        service=SqlAuthService(),
        store=store,
        identity="sql-integration",
        clock=lambda: datetime(2030, 1, 1, tzinfo=UTC),
    )

    class ProtectedApi(AsyncApi):
        @api.get(
            "/account",
            operation_id="sqlProtected",
            responses=Responses[NormalizedResponse[object]](success=()),
            security=auth.scheme,
            raw_response=True,
        )
        async def account(self) -> NormalizedResponse[object]:
            raise NotImplementedError

    client = client_from_httpx(
        httpx.AsyncClient(
            base_url="https://api.example",
            transport=httpx.MockTransport(handler),
            headers={},
            cookies={},
        ),
        config=ClientConfig(auth=auth),
    )
    client.bind_sdk(SqlSdk)
    response = await ProtectedApi(client).account()
    await client.aclose()

    assert response.json() == {"authorization": "Bearer access-v2"}
    assert [path for path, _ in calls] == ["/login", "/account", "/refresh", "/account"]
    stored = await store.load(SessionKey("session:sql-integration"))
    assert stored is not None
    assert stored.revision == SessionRevision(2)
    assert stored.value.access_token.get_secret_value() == "access-v2"


async def test_bearer_cookie_and_browser_payloads_use_same_session_store_path(
    session: AsyncSession,
) -> None:
    account = Account(identifier="opaque-session-owner")
    session.add(account)
    await session.commit()
    store = SqlSessionStore(session, account.id, session_model=OpaqueSession)
    values = {
        "bearer": OpaqueSession(state={"access_token": "access-v1"}),
        "cookie": OpaqueSession(state={"cookies": [{"name": "sid", "value": "cookie-v1"}]}),
        "browser": OpaqueSession(state={"storage_state": {"cookies": [], "origins": []}}),
    }
    for key, value in values.items():
        await store.save(SessionKey(key), value, SessionRevision(1))
    for key, value in values.items():
        loaded = await store.load(SessionKey(key))
        assert loaded is not None and loaded.value == value
    rows = (await session.execute(select(Session))).scalars().all()
    assert {row.key for row in rows} == set(values)
    assert all(set(row.payload) == {"format", "version", "data"} for row in rows)


async def test_verification_history_has_explicit_via_and_replaces(session: AsyncSession) -> None:
    account = Account(identifier="target")
    mailbox = Account(provider="mail", identifier="ada@example.test")
    session.add_all([account, mailbox])
    await session.flush()
    first = Verification(
        account_id=account.id,
        via_account_id=mailbox.id,
        challenge_id="email-1",
        kind="email_code",
    )
    session.add(first)
    await session.flush()
    first.status = "superseded"
    second = Verification(
        account_id=account.id,
        via_account_id=mailbox.id,
        challenge_id="email-2",
        kind="email_code",
        replaces_id=first.id,
    )
    session.add(second)
    await session.flush()
    assert second.replaces_id == first.id
    assert second.via_account_id == mailbox.id


async def test_state_and_event_rollback_together(session: AsyncSession) -> None:
    account = Account(identifier="rollback")
    with pytest.raises(RuntimeError):
        async with session.begin_nested():
            session.add(account)
            await session.flush()
            session.add(AccountEvent(account_id=account.id, type="registration.started"))
            raise RuntimeError("boom")
    assert (await session.execute(select(Account).where(Account.id == account.id))).first() is None
    assert (await session.execute(select(AccountEvent))).first() is None
    assert not hasattr(build_sqlmodel_storage(session).events, "update")
    assert not hasattr(build_sqlmodel_storage(session).events, "delete")


async def test_open_workspace_owns_transaction_not_engine(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with open_workspace(engine) as workspace:
            account = await workspace.accounts.create(
                "ada",
                credentials={"format": "plain-json", "version": 1, "data": {}},
            )
            assert await workspace.accounts.get(account.id) is not None
        async with open_workspace(engine) as workspace:
            assert await workspace.accounts.get_by_identifier("ada") is not None
    finally:
        await engine.dispose()


async def test_two_sessions_reserve_exactly_once(tmp_path: Path) -> None:
    path = tmp_path / "concurrency.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 5})
    await create_all(engine)
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        owner1 = Account(identifier="owner-c1")
        owner2 = Account(identifier="owner-c2")
        resource = Account(identifier="resource-c")
        setup.add_all([owner1, owner2, resource])
        await setup.commit()
        ids = owner1.id, owner2.id, resource.id

    async def attempt(owner_id) -> bool:  # type: ignore[no-untyped-def]
        async with AsyncSession(engine, expire_on_commit=False) as db:
            storage = build_sqlmodel_storage(db)
            try:
                await storage.links.reserve(
                    owner_account_id=owner_id,
                    resource_account_id=ids[2],
                    relation="registration_identity",
                    exclusive_scope="registration",
                )
                await db.commit()
                return True
            except SqlResourceConflictError:
                await db.rollback()
                return False

    try:
        results = await asyncio.gather(attempt(ids[0]), attempt(ids[1]))
        assert sorted(results) == [False, True]
    finally:
        await engine.dispose()


async def test_two_sessions_create_default_provider_identity_exactly_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity-concurrency.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 5})
    await create_all(engine)

    async def attempt() -> bool:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            accounts = build_sqlmodel_storage(db).accounts
            try:
                await accounts.create({"identifier": "same", "provider": None})
                await db.commit()
                return True
            except DuplicateAccountError:
                await db.rollback()
                return False

    try:
        results = await asyncio.gather(attempt(), attempt())
        assert sorted(results) == [False, True]
        async with AsyncSession(engine) as db:
            rows = (await db.execute(select(Account))).scalars().all()
            assert len(rows) == 1 and rows[0].provider == DEFAULT_PROVIDER
    finally:
        await engine.dispose()


def test_legacy_columns_and_restriction_model_are_absent() -> None:
    account_columns = set(Account.model_fields)
    session_columns = set(Session.model_fields)
    assert not {"cred_scheme", "cred_secret", "cred_data"} & account_columns
    assert not {"headers", "cookies", "params", "label"} & session_columns
    assert "eazy_sdk_restrictions" not in SQLModel.metadata.tables


def test_custom_model_bundle_is_validated_before_first_write(session: AsyncSession) -> None:
    from eazy_sdk_sqlmodel.factory import SqlStorageModels

    with pytest.raises(SqlModelConfigurationError, match="concrete table model"):
        build_sqlmodel_storage(session, models=SqlStorageModels(account=SQLModel))


def test_postgresql_schema_snapshot() -> None:
    snapshot = Path(__file__).with_name("snapshots") / "account_storage_v2.postgresql.sql"
    assert schema_ddl("postgresql") == snapshot.read_text(encoding="utf-8")
