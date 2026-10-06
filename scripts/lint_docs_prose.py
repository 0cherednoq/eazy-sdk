"""Run humanizer-ru on prose extracted from one documentation page."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_LINTER = Path.home() / ".claude" / "skills" / "humanizer-ru" / "scripts" / "lint.py"
CODE_FENCE = re.compile(r"^\s*(?P<marker>`{3,}|~{3,})(?P<label>.*)$")
DIRECTIVE_OPEN = re.compile(r"^\s*(?P<marker>:{3,})\{[^}]+\}\s*$")
DIRECTIVE_CLOSE = re.compile(r"^\s*(?P<marker>:{3,})\s*$")
INLINE_CODE = re.compile(r"`+[^`\n]+`+")
MARKDOWN_LINK = re.compile(r"\[([^]]+)]\([^)]+\)")
HTML_COMMENT = re.compile(r"<!--.*?-->")


def extract_prose(source: str) -> str:
    """Remove non-prose MDX while retaining original line numbers."""

    lines = source.splitlines()
    output: list[str] = []
    frontmatter = bool(lines and lines[0].strip() == "---")
    code_marker: str | None = None
    directive_markers: list[int] = []

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if frontmatter:
            output.append("")
            if line_number > 1 and stripped == "---":
                frontmatter = False
            continue

        if code_marker is not None:
            output.append("")
            fence = CODE_FENCE.match(line)
            if fence is not None:
                marker = fence.group("marker")
                if marker[0] == code_marker[0] and len(marker) >= len(code_marker):
                    code_marker = None
            continue

        directive_open = DIRECTIVE_OPEN.match(line)
        if directive_open is not None:
            directive_markers.append(len(directive_open.group("marker")))
            output.append("")
            continue
        directive_close = DIRECTIVE_CLOSE.match(line)
        if directive_close is not None:
            marker_length = len(directive_close.group("marker"))
            if directive_markers and marker_length == directive_markers[-1]:
                directive_markers.pop()
            output.append("")
            continue
        if directive_markers:
            output.append("")
            continue

        fence = CODE_FENCE.match(line)
        if fence is not None:
            code_marker = fence.group("marker")
            output.append("")
            continue
        if stripped.startswith(":") or stripped.startswith("<!--"):
            output.append("")
            continue

        prose = HTML_COMMENT.sub("", line)
        prose = INLINE_CODE.sub("", prose)
        prose = MARKDOWN_LINK.sub(r"\1", prose)
        output.append(prose)

    suffix = "\n" if source.endswith("\n") else ""
    return "\n".join(output) + suffix


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", type=Path, help="one .md or .mdx page to lint")
    parser.add_argument(
        "--linter",
        type=Path,
        default=Path(os.environ.get("HUMANIZER_RU_LINTER", DEFAULT_LINTER)),
        help="path to humanizer-ru scripts/lint.py",
    )
    parser.add_argument(
        "--print-prose",
        action="store_true",
        help="print extracted prose instead of invoking the linter",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    page = args.page.resolve()
    if page.suffix.lower() not in {".md", ".mdx"} or not page.is_file():
        _parser().error(f"page does not exist or is not Markdown: {page}")

    prose = extract_prose(page.read_text(encoding="utf-8"))
    if args.print_prose:
        sys.stdout.write(prose)
        return 0
    if not args.linter.is_file():
        _parser().error(
            f"humanizer-ru linter not found: {args.linter}; set HUMANIZER_RU_LINTER"
        )

    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    print(f"humanizer-ru: {page}", flush=True)
    completed = subprocess.run(
        [sys.executable, str(args.linter)],
        input=prose,
        env=environment,
        check=False,
        encoding="utf-8",
        text=True,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
