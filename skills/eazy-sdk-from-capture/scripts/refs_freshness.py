"""Check the skill's references against the installed library.

    python refs_freshness.py [REFERENCES_DIR]

Every ``import`` of ``eazy_sdk`` or one of its integrations inside a Python code block of a
reference page must resolve in the environment the script runs in. A reference that names
something the library no longer has would teach the model to write code that does not import.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

_BLOCK = re.compile(r"```python[^\n]*\n(.*?)```", re.DOTALL)
_LIBRARY = re.compile(r"^(eazy_sdk|eazy_sdk_[a-z]+)(\.|$)")


def code_blocks(text: str) -> Iterator[tuple[int, str]]:
    """Yield the Python code blocks of a Markdown page with their first line number."""
    for match in _BLOCK.finditer(text):
        yield text.count("\n", 0, match.start(1)) + 1, match.group(1)


def imports_of(source: str) -> Iterator[tuple[int, str, str | None]]:
    """Yield ``(line, module, name)`` for every library import of a code block."""
    try:
        nodes = [(node, 0) for node in ast.walk(ast.parse(source))]
    except SyntaxError:
        # A fragment that is not a whole module: read its single-line imports one by one.
        nodes = []
        for number, line in enumerate(source.splitlines()):
            if line.startswith(("from ", "import ")) and not line.rstrip().endswith("("):
                try:
                    nodes += [(node, number) for node in ast.walk(ast.parse(line))]
                except SyntaxError:
                    continue
    for node, offset in nodes:
        yield from _library_imports(node, offset)


def _library_imports(node: ast.AST, offset: int) -> Iterator[tuple[int, str, str | None]]:
    if isinstance(node, ast.ImportFrom) and node.module and _LIBRARY.match(node.module):
        for alias in node.names:
            yield node.lineno + offset, node.module, alias.name
    elif isinstance(node, ast.Import):
        for alias in node.names:
            if _LIBRARY.match(alias.name):
                yield node.lineno + offset, alias.name, None


def resolve(module: str, name: str | None) -> str | None:
    """Return why an import does not resolve, or ``None`` when it does."""
    try:
        imported = importlib.import_module(module)
    except ImportError as error:
        return f"module {module} does not import: {error}"
    if name is None or name == "*" or hasattr(imported, name):
        return None
    try:
        importlib.import_module(f"{module}.{name}")
    except ImportError:
        return f"{module} has no {name}"
    return None


def check(references: Path) -> list[str]:
    """List every stale import of the reference pages; empty means fresh."""
    problems: list[str] = []
    for page in sorted(references.glob("*.md")):
        for first_line, block in code_blocks(page.read_text(encoding="utf-8")):
            for line, module, name in imports_of(block):
                reason = resolve(module, name)
                if reason:
                    problems.append(f"{page.name}:{first_line + line - 1}: {reason}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Check the references directory of this skill, or the one given."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    default = Path(__file__).resolve().parents[1] / "references"
    references = Path(arguments[0]) if arguments else default
    problems = check(references)
    for problem in problems:
        print(problem)
    pages = len(list(references.glob("*.md")))
    print(f"{pages} reference pages, {len(problems)} stale imports")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
