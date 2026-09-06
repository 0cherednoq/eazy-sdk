"""Phase 50.1.1: the name-budget metric is a reproducible command, not a number in a document."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "surface_count.py"


def test_surface_count_script_runs() -> None:
    """I13: the script prints one integer total and a per-module table, and the total is bounded."""

    total = subprocess.run(
        [sys.executable, str(SCRIPT), "--total"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    count = int(total.stdout.strip())
    assert count > 0
    table = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = table.stdout.strip().splitlines()
    assert lines[0].startswith("eazy_sdk ")
    assert lines[-1].startswith("distinct public names")
    assert lines[-1].split()[-1] == str(count)
    over = subprocess.run(
        [sys.executable, str(SCRIPT), "--max", str(count - 1)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert over.returncode == 1
    assert "surface budget exceeded" in over.stderr
