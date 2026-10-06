"""Keep teaching literals on reserved ``example`` domains."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCAN_ROOTS = (
    ROOT / "docs-site" / "src" / "content" / "docs",
    ROOT / "examples",
)
DEBT_FILE = ROOT / "docs" / "implementation" / "56-domain-debt.txt"
TEXT_SUFFIXES = {".html", ".json", ".md", ".mdx", ".py", ".toml", ".yaml", ".yml"}
MARKDOWN_LINK = re.compile(r"\]\((?:https?|wss?)://[^)]+\)")
DOMAIN_LITERAL = re.compile(
    r"(?:(?:https?|wss?)://|[A-Za-z0-9._%+-]+@)"
    r"(?P<host>[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)
RESERVED_SECOND_LEVEL = ("example.com", "example.net", "example.org")


@dataclass(frozen=True, slots=True, order=True)
class DomainDebt:
    path: str
    line: int
    host: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.host}"


def _is_example_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    return (
        normalized == "example"
        or normalized.endswith(".example")
        or any(
            normalized == reserved or normalized.endswith(f".{reserved}")
            for reserved in RESERVED_SECOND_LEVEL
        )
    )


def _domain_debt() -> list[DomainDebt]:
    debt: list[DomainDebt] = []
    for scan_root in SCAN_ROOTS:
        for path in scan_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            for line_number, original_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                line = original_line
                if path.suffix.lower() in {".md", ".mdx"}:
                    line = MARKDOWN_LINK.sub("]", line)
                for match in DOMAIN_LITERAL.finditer(line):
                    host = match.group("host").lower()
                    if not _is_example_host(host):
                        debt.append(
                            DomainDebt(path.relative_to(ROOT).as_posix(), line_number, host)
                        )
    return sorted(debt)


def _recorded_debt() -> list[str]:
    return [
        line
        for line in DEBT_FILE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def test_only_example_domains_appear_in_teaching_literals() -> None:
    assert [item.render() for item in _domain_debt()] == _recorded_debt()
