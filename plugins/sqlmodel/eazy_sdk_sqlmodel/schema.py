"""Deterministic DDL rendering for account-storage release audits."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlmodel import SQLModel

from eazy_sdk_sqlmodel import tables


def schema_ddl(dialect: str) -> str:
    """Render the five default tables and their indexes in dependency order."""

    if dialect == "postgresql":
        compiler_dialect: Dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    elif dialect == "sqlite":
        compiler_dialect = sqlite.dialect()
    else:
        raise ValueError(f"unsupported schema snapshot dialect: {dialect}")
    rendered: list[str] = []
    for table in _ordered_tables():
        rendered.append(_clean(str(CreateTable(table).compile(dialect=compiler_dialect))) + ";")
        for index in sorted(table.indexes, key=lambda value: value.name or ""):
            rendered.append(_clean(str(CreateIndex(index).compile(dialect=compiler_dialect))) + ";")
    return "\n\n".join(rendered) + "\n"


def _clean(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.strip().splitlines())


def _ordered_tables() -> tuple[Table, ...]:
    models: tuple[type[SQLModel], ...] = (
        tables.Account,
        tables.Session,
        tables.Verification,
        tables.AccountLink,
        tables.AccountEvent,
    )
    return tuple(cast(Table, cast(Any, model).__table__) for model in models)


__all__ = ["schema_ddl"]
