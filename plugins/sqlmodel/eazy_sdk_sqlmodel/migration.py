"""Explicit one-shot migration from the historical SQLModel schema to account storage v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlmodel import SQLModel

from eazy_sdk_sqlmodel import tables
from eazy_sdk_sqlmodel.models import normalize_identifier, normalize_provider

V1_TABLES = (
    "eazy_sdk_accounts",
    "eazy_sdk_sessions",
    "eazy_sdk_verifications",
    "eazy_sdk_restrictions",
    "eazy_sdk_account_links",
    "eazy_sdk_account_events",
)


@dataclass(frozen=True, slots=True)
class MigrationIssue:
    code: str
    table: str
    row_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True, slots=True)
class MigrationReport:
    source_tables: tuple[str, ...]
    row_counts: Mapping[str, int]
    issues: tuple[MigrationIssue, ...]
    already_applied: bool = False

    @property
    def can_apply(self) -> bool:
        return bool(self.source_tables) and not self.issues and not self.already_applied


class MigrationError(RuntimeError):
    pass


class MigrationConflictError(MigrationError):
    def __init__(self, report: MigrationReport) -> None:
        self.report = report
        super().__init__(f"v1 storage migration has {len(report.issues)} conflict(s)")


class MigrationAlreadyAppliedError(MigrationError):
    pass


async def inspect_v1(engine: AsyncEngine) -> MigrationReport:
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        source_names = tuple(name for name in V1_TABLES if name in names)
        target_present = bool(tables.TABLE_NAMES & names)
        if not source_names:
            return MigrationReport((), {}, (), already_applied=target_present)
        metadata = MetaData()
        await connection.run_sync(lambda sync: metadata.reflect(sync, only=source_names))
        rows = await _read_rows(connection, metadata, source_names)
        issues = _issues(rows, target_present=target_present)
        return MigrationReport(
            source_tables=source_names,
            row_counts={name: len(values) for name, values in rows.items()},
            issues=tuple(issues),
        )


def _issues(
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    target_present: bool,
) -> list[MigrationIssue]:
    issues: list[MigrationIssue] = []
    accounts = rows.get("eazy_sdk_accounts", ())
    account_ids = {str(row["id"]) for row in accounts}
    identities: dict[tuple[str, str], list[str]] = {}
    remote_ids: dict[tuple[str, str], list[str]] = {}
    for row in accounts:
        row_id = str(row["id"])
        try:
            identity = (
                normalize_provider(_optional_str(row.get("provider"))),
                normalize_identifier(str(row.get("identifier", ""))),
            )
        except ValueError:
            issues.append(
                MigrationIssue(
                    "invalid_identifier",
                    "eazy_sdk_accounts",
                    (row_id,),
                    "account identifier is empty after normalization",
                )
            )
            continue
        identities.setdefault(identity, []).append(row_id)
        registration = _mapping(_mapping(row.get("meta")).get("registration"))
        remote_id = registration.get("remote_id")
        if isinstance(remote_id, str) and remote_id:
            remote_ids.setdefault((identity[0], remote_id), []).append(row_id)
    issues.extend(_duplicates("duplicate_identity", "eazy_sdk_accounts", identities))
    issues.extend(_duplicates("duplicate_remote_id", "eazy_sdk_accounts", remote_ids))

    sessions = rows.get("eazy_sdk_sessions", ())
    active_keys: dict[tuple[str, str], list[str]] = {}
    for row in sessions:
        if bool(row.get("is_active", True)):
            key = str(row.get("label") or f"legacy:{row['id']}")
            active_keys.setdefault((str(row.get("account_id")), key), []).append(str(row["id"]))
    issues.extend(_duplicates("duplicate_active_session_key", "eazy_sdk_sessions", active_keys))

    for table_name, columns in {
        "eazy_sdk_sessions": ("account_id",),
        "eazy_sdk_verifications": ("account_id",),
        "eazy_sdk_account_links": ("account_id", "linked_account_id"),
        "eazy_sdk_account_events": ("account_id",),
    }.items():
        for row in rows.get(table_name, ()):
            missing = [column for column in columns if str(row.get(column)) not in account_ids]
            if missing:
                issues.append(
                    MigrationIssue(
                        "orphan_reference",
                        table_name,
                        (str(row["id"]),),
                        f"row references missing account columns: {', '.join(missing)}",
                    )
                )

    restrictions = rows.get("eazy_sdk_restrictions", ())
    if restrictions:
        issues.append(
            MigrationIssue(
                "restriction_policy_required",
                "eazy_sdk_restrictions",
                tuple(str(row["id"]) for row in restrictions),
                "restriction rows require an explicit application migration policy",
            )
        )
    if target_present:
        issues.append(
            MigrationIssue(
                "target_schema_exists",
                "accounts",
                (),
                "v2 tables already exist while v1 tables are still present",
            )
        )
    return issues


def _duplicates[K](
    code: str,
    table: str,
    groups: Mapping[K, Sequence[str]],
) -> list[MigrationIssue]:
    return [
        MigrationIssue(code, table, tuple(row_ids), "multiple rows normalize to one v2 key")
        for row_ids in groups.values()
        if len(row_ids) > 1
    ]


async def migrate_v1_to_v2(engine: AsyncEngine, *, dry_run: bool = False) -> MigrationReport:
    report = await inspect_v1(engine)
    if dry_run:
        return report
    if report.already_applied:
        raise MigrationAlreadyAppliedError("v1 storage migration was already applied")
    if not report.source_tables:
        raise MigrationError("no v1 SQLModel tables were found")
    if report.issues:
        raise MigrationConflictError(report)

    async with engine.begin() as connection:
        metadata = MetaData()
        await connection.run_sync(lambda sync: metadata.reflect(sync, only=report.source_tables))
        source_rows = await _read_rows(connection, metadata, report.source_tables)
        await connection.run_sync(
            lambda sync: SQLModel.metadata.create_all(
                sync,
                tables=[
                    _table(tables.Account),
                    _table(tables.Session),
                    _table(tables.Verification),
                    _table(tables.AccountLink),
                    _table(tables.AccountEvent),
                ],
            )
        )
        await _copy_rows(connection, source_rows)
        await connection.run_sync(
            lambda sync: metadata.drop_all(
                sync,
                tables=[metadata.tables[name] for name in reversed(report.source_tables)],
            )
        )
    return report


async def _read_rows(
    connection: AsyncConnection,
    metadata: MetaData,
    names: Sequence[str],
) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    for name in names:
        rows = (await connection.execute(select(metadata.tables[name]))).mappings().all()
        result[name] = [cast(Mapping[str, Any], dict(row)) for row in rows]
    return result


async def _copy_rows(
    connection: AsyncConnection,
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    account_values = [_account_row(row) for row in rows.get("eazy_sdk_accounts", ())]
    if account_values:
        await connection.execute(_table(tables.Account).insert(), account_values)

    session_values = [_session_row(row) for row in rows.get("eazy_sdk_sessions", ())]
    if session_values:
        await connection.execute(_table(tables.Session).insert(), session_values)

    verification_values = [_verification_row(row) for row in rows.get("eazy_sdk_verifications", ())]
    if verification_values:
        await connection.execute(_table(tables.Verification).insert(), verification_values)

    link_values = [_link_row(row) for row in rows.get("eazy_sdk_account_links", ())]
    if link_values:
        await connection.execute(_table(tables.AccountLink).insert(), link_values)

    event_values = [_event_row(row) for row in rows.get("eazy_sdk_account_events", ())]
    if event_values:
        await connection.execute(_table(tables.AccountEvent).insert(), event_values)


def _table(model: type[SQLModel]) -> Table:
    return cast(Table, cast(Any, model).__table__)


def _account_row(row: Mapping[str, Any]) -> dict[str, Any]:
    meta = _mapping(row.get("meta"))
    registration = _mapping(meta.pop("registration", {}))
    registration_meta = _mapping(registration.get("meta"))
    if registration_meta:
        meta["registration"] = registration_meta
    credentials = {
        "format": "plain-json",
        "version": 1,
        "data": {
            "scheme": row.get("cred_scheme"),
            "secret": row.get("cred_secret"),
            "data": _mapping(row.get("cred_data")),
        },
    }
    return {
        "id": _uuid(row["id"]),
        "provider": normalize_provider(_optional_str(row.get("provider"))),
        "identifier": normalize_identifier(str(row["identifier"])),
        "remote_id": _optional_str(registration.get("remote_id")),
        "status": str(row.get("status") or "active"),
        "revision": _integer(registration.get("revision"), default=0),
        "credentials": credentials,
        "profile": _mapping(registration.get("profile")),
        "meta": meta,
        "created_at": _datetime(row.get("created_at")),
        "updated_at": _datetime(row.get("updated_at")),
    }


def _session_row(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "format": "legacy-session",
        "version": 1,
        "data": {
            "headers": _mapping(row.get("headers")),
            "cookies": _mapping(row.get("cookies")),
            "params": _mapping(row.get("params")),
            "meta": _mapping(row.get("meta")),
            "scopes": list(row.get("scopes_list") or []),
            "audience": row.get("audience"),
            "subject": row.get("subject"),
        },
    }
    return {
        "id": _uuid(row["id"]),
        "account_id": _uuid(row["account_id"]),
        "key": str(row.get("label") or f"legacy:{row['id']}"),
        "revision": 1,
        "kind": str(row.get("scheme") or "custom"),
        "payload": payload,
        "expires_at": _datetime(row.get("expires_at"), optional=True),
        "is_active": bool(row.get("is_active", True)),
        "created_at": _datetime(row.get("created_at")),
        "updated_at": _datetime(row.get("updated_at")),
    }


def _verification_row(row: Mapping[str, Any]) -> dict[str, Any]:
    legacy_meta = _mapping(row.get("meta"))
    status = str(row.get("status") or "pending")
    return {
        "id": _uuid(row["id"]),
        "account_id": _uuid(row["account_id"]),
        "via_account_id": None,
        "challenge_id": str(legacy_meta.get("challenge_id") or row["id"]),
        "kind": str(row.get("type") or "custom"),
        "status": "accepted" if status == "verified" else status,
        "target": _optional_str(row.get("target")),
        "expires_at": _datetime(legacy_meta.get("expires_at"), optional=True),
        "attempts_remaining": _optional_integer(legacy_meta.get("attempts_remaining")),
        "replaces_id": None,
        "meta": _mapping(legacy_meta.get("meta")),
        "created_at": _datetime(row.get("created_at")),
        "updated_at": _datetime(row.get("updated_at")),
        "verified_at": _datetime(row.get("verified_at"), optional=True),
    }


def _link_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _uuid(row["id"]),
        "owner_account_id": _uuid(row["account_id"]),
        "resource_account_id": _uuid(row["linked_account_id"]),
        "relation": str(row.get("type") or "custom"),
        "status": "active",
        "exclusive_scope": None,
        "meta": _mapping(row.get("meta")),
        "created_at": _datetime(row.get("created_at")),
        "updated_at": _datetime(row.get("updated_at")),
        "released_at": None,
    }


def _event_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _uuid(row["id"]),
        "account_id": _uuid(row["account_id"]),
        "type": str(row.get("type") or "legacy.unknown"),
        "occurred_at": _datetime(row.get("occurred_at")),
        "correlation_id": None,
        "session_id": _optional_uuid(row.get("session_id")),
        "verification_id": None,
        "link_id": None,
        "data": _mapping(row.get("data")),
        "meta": _mapping(row.get("meta")),
    }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_uuid(value: object) -> UUID | None:
    return _uuid(value) if value is not None else None


def _integer(value: object, *, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _optional_integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _datetime(value: object, *, optional: bool = False) -> datetime | None:
    if value is None:
        if optional:
            return None
        raise MigrationError("required v1 timestamp is missing")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise MigrationError("v1 timestamp has an unsupported type")


__all__ = [
    "MigrationAlreadyAppliedError",
    "MigrationConflictError",
    "MigrationError",
    "MigrationIssue",
    "MigrationReport",
    "inspect_v1",
    "migrate_v1_to_v2",
]
