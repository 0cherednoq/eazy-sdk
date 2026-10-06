"""Keep runnable phase-56 examples and their documented output in sync."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
EXAMPLES_ROOT = ROOT / "examples" / "mail"
DOCS_ROOTS = (
    ROOT / "docs-site" / "src" / "content" / "docs",
    ROOT / "docs-site" / "probes",
)
OUTPUT_BLOCK = re.compile(
    r"<!--\s*example-output:\s*(?P<module>[a-zA-Z0-9_.]+)\s*-->"
    r"\s*```text\s*\n(?P<output>.*?)\n```",
    re.DOTALL,
)
MAIN_GUARD = 'if __name__ == "__main__":'


@dataclass(frozen=True, slots=True)
class DocumentedOutput:
    module: str
    output: str
    page: Path


def _documented_outputs() -> list[DocumentedOutput]:
    records: list[DocumentedOutput] = []
    for docs_root in DOCS_ROOTS:
        for page in docs_root.rglob("*.mdx"):
            text = page.read_text(encoding="utf-8")
            records.extend(
                DocumentedOutput(
                    module=match.group("module"),
                    output=match.group("output"),
                    page=page.relative_to(ROOT),
                )
                for match in OUTPUT_BLOCK.finditer(text)
            )
    return records


def _runnable_example_modules() -> set[str]:
    modules: set[str] = set()
    for source in EXAMPLES_ROOT.rglob("*.py"):
        if source.name == "__main__.py":
            continue
        if MAIN_GUARD not in source.read_text(encoding="utf-8"):
            continue
        modules.add(".".join(source.relative_to(ROOT).with_suffix("").parts))
    return modules


DOCUMENTED_OUTPUTS = _documented_outputs()


def test_each_runnable_example_has_one_output_block() -> None:
    documented = [record.module for record in DOCUMENTED_OUTPUTS]
    assert len(documented) == len(set(documented)), "example-output modules must be unique"
    assert set(documented) == _runnable_example_modules()


@pytest.mark.parametrize(
    "record",
    DOCUMENTED_OUTPUTS,
    ids=lambda record: f"{record.module} in {record.page}",
)
def test_documented_example_output_matches_module(record: DocumentedOutput) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", record.module],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.rstrip("\n") == record.output
