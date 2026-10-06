"""Characterize prose that must be rewritten during phase 56."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCS_ROOT = ROOT / "docs-site" / "src" / "content" / "docs"
DEBT_FILE = ROOT / "docs" / "implementation" / "56-prose-debt.txt"
CODE_FENCE = re.compile(r"^\s*(?P<marker>`{3,}|~{3,})")
DIRECTIVE_OPEN = re.compile(r"^\s*(?P<marker>:{3,})\{[^}]+\}\s*$")
DIRECTIVE_CLOSE = re.compile(r"^\s*(?P<marker>:{3,})\s*$")
INLINE_CODE = re.compile(r"`+[^`]+`+")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
LATIN_WORD = re.compile(r"\b[A-Za-z]{2,}\b")
CYRILLIC_WORD = re.compile(r"[А-Яа-яЁё]{2,}")


@dataclass(frozen=True, slots=True, order=True)
class ProseViolation:
    page: str
    line: int
    rule: str
    count: int = 1

    def render(self) -> str:
        location = f"{self.page}:{self.line}" if self.line else self.page
        suffix = f"={self.count}" if self.rule == "em-dash" else ""
        return f"{location}: {self.rule}{suffix}"


def _prose_lines(page: Path) -> list[tuple[int, str] | None]:
    lines = page.read_text(encoding="utf-8").splitlines()
    prose: list[tuple[int, str] | None] = []
    frontmatter = bool(lines and lines[0].strip() == "---")
    code_marker: str | None = None
    directive_markers: list[int] = []

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if frontmatter:
            if line_number > 1 and stripped == "---":
                frontmatter = False
            continue

        code_fence = CODE_FENCE.match(line)
        if code_fence is not None and not directive_markers:
            marker = code_fence.group("marker")
            if code_marker is None:
                code_marker = marker
            elif marker[0] == code_marker[0] and len(marker) >= len(code_marker):
                code_marker = None
            prose.append(None)
            continue
        if code_marker is not None:
            continue

        directive_open = DIRECTIVE_OPEN.match(line)
        if directive_open is not None:
            directive_markers.append(len(directive_open.group("marker")))
            prose.append(None)
            continue
        directive_close = DIRECTIVE_CLOSE.match(line)
        if directive_close is not None and directive_markers:
            marker_length = len(directive_close.group("marker"))
            if marker_length == directive_markers[-1]:
                directive_markers.pop()
            prose.append(None)
            continue
        if directive_markers:
            continue

        if (
            not stripped
            or stripped.startswith(("#", ":", "<!--"))
            or stripped.startswith("    ")
            or re.fullmatch(r"\|?[\s:|-]+\|?", stripped)
        ):
            prose.append(None)
            continue
        prose.append((line_number, line))
    return prose


def _plain_text(text: str) -> str:
    text = INLINE_CODE.sub("", text)
    text = LINK_TARGET.sub("]", text)
    return URL.sub("", text)


def _violations() -> list[ProseViolation]:
    violations: list[ProseViolation] = []
    for page in DOCS_ROOT.rglob("*.mdx"):
        relative = page.relative_to(DOCS_ROOT).as_posix()
        prose_lines = _prose_lines(page)
        dash_count = sum(line.count("—") for item in prose_lines if item for _, line in [item])
        if dash_count:
            violations.append(ProseViolation(relative, 0, "em-dash", dash_count))

        paragraph: list[tuple[int, str]] = []
        for item in [*prose_lines, None]:
            if item is not None:
                paragraph.append(item)
                continue
            if not paragraph:
                continue
            text = _plain_text(" ".join(line for _, line in paragraph))
            if len(LATIN_WORD.findall(text)) >= 4 and not CYRILLIC_WORD.search(text):
                violations.append(
                    ProseViolation(relative, paragraph[0][0], "english-paragraph")
                )
            paragraph = []
    return sorted(violations)


def _recorded_debt() -> list[str]:
    return [
        line
        for line in DEBT_FILE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def _debt_summary() -> list[str]:
    counts: Counter[tuple[str, str]] = Counter()
    for violation in _violations():
        increment = violation.count if violation.rule == "em-dash" else 1
        counts[(violation.page, violation.rule)] += increment
    return [
        f"{page}: {rule}={count}"
        for (page, rule), count in sorted(counts.items())
    ]


def test_prose_debt_snapshot_is_current() -> None:
    assert _debt_summary() == _recorded_debt()


def test_docs_prose_has_no_em_dashes_or_english_paragraphs() -> None:
    violations = _violations()
    counts = Counter(violation.rule for violation in violations)
    assert not violations, f"{dict(counts)}\n" + "\n".join(
        violation.render() for violation in violations
    )
