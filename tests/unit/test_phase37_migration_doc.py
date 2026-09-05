"""Phase 37: the migration page stays true — every "стало" resolves, every "было" is gone."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

PAGE = (
    Path(__file__).resolve().parents[2]
    / "docs-site/src/content/docs/more/migration.mdx"
)
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|")


def _rows() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in PAGE.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match is None:
            continue
        old, new = match.group(1), match.group(2)
        if "." in old and "." in new:
            rows.append((old, new))
    return rows


def _resolve(path: str) -> object | None:
    """Import the longest module prefix, then walk attributes; ``None`` when absent."""

    parts = path.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            value: object = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for attribute in parts[cut:]:
            if not hasattr(value, attribute):
                return None
            value = getattr(value, attribute)
        return value
    return None


@pytest.mark.parametrize(("old", "new"), _rows(), ids=lambda item: item)
def test_migration_row_is_accurate(old: str, new: str) -> None:
    for name in ("eazy_sdk_html", "eazy_sdk_accounts"):
        if new.startswith(name):
            pytest.importorskip(name)
    assert _resolve(new) is not None, f"{new} does not resolve"
    assert _resolve(old) is None, f"{old} still exists"


def test_migration_page_covers_both_alpha_steps() -> None:
    assert len(_rows()) >= 20
    text = PAGE.read_text(encoding="utf-8")
    assert "## 0.2.0a4 → 0.2.0a5" in text and "## 0.2.0a3 → 0.2.0a4" in text
