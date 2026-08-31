"""Validate metadata and minimum content for every published docs page."""

from __future__ import annotations

import re
import sys
from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parents[1] / "src" / "content" / "docs"
FRONTMATTER = re.compile(r"\A---\r?\n([\s\S]*?)\r?\n---\r?\n([\s\S]*)\Z")
PAGE_SUFFIXES = {".md", ".mdx"}


def _scalar(block: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.*?)\s*$", block)
    if match is None:
        return None
    return match.group(1).strip("'\"")


def _sources(block: str) -> list[str]:
    match = re.search(r"(?ms)^sources:\s*\n((?:\s+-\s+.*\n?)+)", block)
    if match is None:
        return []
    return [line.strip()[2:].strip() for line in match.group(1).splitlines()]


def main() -> int:
    errors: list[str] = []
    pages = 0
    for path in sorted(DOCS_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in PAGE_SUFFIXES:
            continue
        pages += 1
        relative = path.relative_to(DOCS_ROOT).as_posix()
        match = FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if match is None:
            errors.append(f"{relative}: нет frontmatter")
            continue
        metadata, body = match.groups()
        draft = _scalar(metadata, "draft") == "true"
        freshness = _scalar(metadata, "freshness") != "false"
        if not draft and len(body.strip()) < 180:
            errors.append(
                f"{relative}: страница выглядит незаполненной "
                f"({len(body.strip())} символов, минимум 180)"
            )
        if draft or not freshness:
            continue
        if not _sources(metadata):
            errors.append(f"{relative}: нет непустого `sources`")
        fingerprint = _scalar(metadata, "api_fingerprint")
        if fingerprint is None or not fingerprint.startswith("sha256:"):
            errors.append(f"{relative}: нет корректного `api_fingerprint`")
    if errors:
        print("Documentation validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Documentation content and frontmatter OK: {pages} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
