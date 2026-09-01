from __future__ import annotations

import ast
import importlib
import re
from collections.abc import Iterator
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
DOCUMENTATION_ROOTS = (
    REPOSITORY / "README.md",
    REPOSITORY / "examples" / "README.md",
    REPOSITORY / "docs" / "implementation",
    REPOSITORY / "docs-site" / "src" / "content" / "docs",
    REPOSITORY / "plugins",
)
EXAMPLES_ROOT = REPOSITORY / "examples"
PYTHON_BLOCK = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
HTTP_DECORATORS = {
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "request",
    "trace",
}


def _markdown_paths() -> Iterator[Path]:
    for root in DOCUMENTATION_ROOTS:
        if root.is_file():
            yield root
            continue
        yield from root.rglob("*.md")
        yield from root.rglob("*.mdx")


def _python_blocks() -> Iterator[tuple[Path, int, str]]:
    for path in _markdown_paths():
        text = path.read_text(encoding="utf-8")
        for match in PYTHON_BLOCK.finditer(text):
            line = text.count("\n", 0, match.start()) + 2
            yield path, line, match.group(1)


def _location(path: Path, line: int) -> str:
    return f"{path.relative_to(REPOSITORY)}:{line}"


def test_documented_http_decorator_blocks_are_self_contained() -> None:
    checked = 0
    for path, line, source in _python_blocks():
        if "@api." not in source:
            continue
        checked += 1
        tree = ast.parse(source, filename=str(path))
        imports_api = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "eazy_sdk"
            and any(alias.name == "api" for alias in node.names)
            for node in ast.walk(tree)
        )
        assert imports_api, f"{_location(path, line)} must import api in the same code block"

    assert checked > 0


def test_documented_eazy_sdk_imports_resolve_to_real_symbols() -> None:
    for path, line, source in _python_blocks():
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            # Some reference blocks intentionally show signature fragments rather than programs.
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module is None or not node.module.startswith("eazy_sdk"):
                continue
            module = importlib.import_module(node.module)
            for alias in node.names:
                if alias.name == "*":
                    continue
                assert hasattr(module, alias.name), (
                    f"{_location(path, line + node.lineno - 1)} imports missing symbol "
                    f"{node.module}.{alias.name}"
                )


def test_documentation_does_not_import_http_decorators_directly() -> None:
    for path, line, source in _python_blocks():
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module not in {"eazy_sdk", "eazy_sdk.api", "eazy_sdk.codegen"}:
                continue
            direct = HTTP_DECORATORS & {alias.name for alias in node.names}
            assert not direct, (
                f"{_location(path, line + node.lineno - 1)} imports HTTP decorators "
                f"directly: {sorted(direct)}"
            )


def _decorated_operations(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == "api"
            and decorator.func.attr in HTTP_DECORATORS
            for decorator in node.decorator_list
        ):
            yield node


def _is_unpack_annotation(annotation: ast.expr | None) -> bool:
    return (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Unpack"
        and isinstance(annotation.slice, ast.Name)
    )


def _assert_supported_operation_style(tree: ast.AST, location: str) -> int:
    checked = 0
    for node in _decorated_operations(tree):
        checked += 1
        positional = [*node.args.posonlyargs, *node.args.args]
        positional_request_parameters = [
            argument.arg
            for argument in positional[1:]
            if argument.arg != "options"
        ]
        assert not positional_request_parameters, (
            f"{location}:{node.lineno} stores request fields as positional parameters: "
            f"{positional_request_parameters}; direct request fields must be keyword-only"
        )
        if node.args.kwarg is not None:
            assert _is_unpack_annotation(node.args.kwarg.annotation), (
                f"{location}:{node.lineno} variadic request input must be Unpack[TypedDict]"
            )
            direct_request_parameters = [
                argument.arg
                for argument in node.args.kwonlyargs
                if argument.arg != "options"
            ]
            assert not direct_request_parameters, (
                f"{location}:{node.lineno} mixes direct request fields with "
                "Unpack[TypedDict]"
            )
    return checked


def test_first_party_api_examples_use_a_supported_request_signature() -> None:
    checked = 0
    for path, line, source in _python_blocks():
        if "@api." not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        checked += _assert_supported_operation_style(tree, _location(path, line))

    for path in EXAMPLES_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        checked += _assert_supported_operation_style(
            tree,
            str(path.relative_to(REPOSITORY)),
        )

    assert checked > 0
