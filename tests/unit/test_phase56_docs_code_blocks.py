"""Reject test scaffolding and fake servers in reader-facing code blocks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCS_ROOT = ROOT / "docs-site" / "src" / "content" / "docs"
DEBT_FILE = ROOT / "docs" / "implementation" / "56-code-block-debt.txt"
FENCE = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})(?P<label>.*)$")
BANNED = {
    "assert": re.compile(r"(?:\bassert\b|\.assert_(?:count|request)\b)"),
    "mock-transport": re.compile(r"\bMockTransport\b"),
    "recording-handler": re.compile(r"\b(?:Async)?RecordingHandler\b"),
    "route-fulfill": re.compile(r"\broute\.fulfill\b"),
    "server-stub": re.compile(r"^\s*class\s+\w*Server\b"),
}


@dataclass(frozen=True, slots=True, order=True)
class Violation:
    page: str
    line: int
    rule: str

    def render(self) -> str:
        return f"{self.page}:{self.line}: {self.rule}"


def _code_block_violations() -> list[Violation]:
    violations: list[Violation] = []
    for page in DOCS_ROOT.rglob("*.mdx"):
        relative = page.relative_to(DOCS_ROOT).as_posix()
        closing_fence: str | None = None
        for line_number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1):
            fence = FENCE.match(line)
            if fence is not None:
                marker = fence.group("fence")
                if closing_fence is None:
                    closing_fence = marker
                elif marker[0] == closing_fence[0] and len(marker) >= len(closing_fence):
                    closing_fence = None
                continue
            if closing_fence is None:
                continue
            violations.extend(
                Violation(relative, line_number, rule)
                for rule, pattern in BANNED.items()
                if pattern.search(line)
            )
    return sorted(violations)


def _recorded_debt() -> list[str]:
    return [
        line
        for line in DEBT_FILE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def test_code_block_debt_snapshot_is_current() -> None:
    assert [violation.render() for violation in _code_block_violations()] == _recorded_debt()


def test_docs_code_blocks_have_no_test_scaffolding() -> None:
    violations = _code_block_violations()
    assert not violations, "\n" + "\n".join(violation.render() for violation in violations)
