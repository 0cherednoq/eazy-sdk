from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from absence_audit import audit  # noqa: E402


def test_removed_execution_architecture_is_absent() -> None:
    assert audit() == []
