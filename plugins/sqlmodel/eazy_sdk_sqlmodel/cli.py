"""Command-line entry point for the one-shot account storage migration."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import create_async_engine

from eazy_sdk_sqlmodel.migration import (
    MigrationConflictError,
    MigrationReport,
    inspect_v1,
    migrate_v1_to_v2,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eazy-sdk-sqlmodel-migrate",
        description="Inspect or migrate Eazy SDK SQLModel account storage v1 to v2.",
    )
    parser.add_argument("url", help="SQLAlchemy async database URL")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the migration; without this flag the command is read-only",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_run(args.url, apply=bool(args.apply)))


async def _run(url: str, *, apply: bool) -> int:
    engine = create_async_engine(url)
    try:
        try:
            report = await (migrate_v1_to_v2(engine) if apply else inspect_v1(engine))
        except MigrationConflictError as exc:
            report = exc.report
            print(_report_json(report))
            return 2
        print(_report_json(report))
        return 0 if (apply or report.can_apply or report.already_applied) else 2
    finally:
        await engine.dispose()


def _report_json(report: MigrationReport) -> str:
    return json.dumps(
        {
            "source_tables": list(report.source_tables),
            "row_counts": dict(report.row_counts),
            "can_apply": report.can_apply,
            "already_applied": report.already_applied,
            "issues": [
                {
                    "code": issue.code,
                    "table": issue.table,
                    "row_ids": list(issue.row_ids),
                    "message": issue.message,
                }
                for issue in report.issues
            ],
        },
        indent=2,
        sort_keys=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
