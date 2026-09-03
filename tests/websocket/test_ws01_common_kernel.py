from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

import pytest

from eazy_sdk.compile import (
    InputField,
    compile_endpoint,
)
from eazy_sdk.core import (
    PlanError,
    RequestLocation,
)
from eazy_sdk.core.kernel import (
    AmbiguousCases,
    CompilerKind,
    CompilerRegistry,
    Malformed,
    MalformedCase,
    NoCaseMatch,
    OperationCallState,
    OperationShape,
    OperationValues,
    SelectedCase,
    ValuePatch,
    ValueSlot,
    arbitrate_cases,
)

ROOT = Path(__file__).parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return imported


def test_common_kernel_has_no_http_or_websocket_runtime_imports() -> None:
    imported = _imports(ROOT / "eazy_sdk" / "core" / "kernel.py")
    forbidden = {
        "zapros",
        "wsproto",
        "eazy_sdk.auth",
        "eazy_sdk.clients",
        "eazy_sdk.request",
        "eazy_sdk.response",
        "eazy_sdk.websocket",
        "eazy_sdk.core.http",
        "eazy_sdk.compile",
        "eazy_sdk.core.http_plan",
    }

    assert not any(
        name == blocked or name.startswith(f"{blocked}.")
        for name in imported
        for blocked in forbidden
    )


def test_common_records_have_no_protocol_specific_fields() -> None:
    assert {item.name for item in fields(ValueSlot)} == {
        "diagnostic_name",
        "validator",
        "required",
        "secret",
        "cardinality",
    }
    assert {item.name for item in fields(OperationShape)} == {"slots"}
    assert {item.name for item in fields(OperationValues)} == {"shape", "_values"}
    assert {item.name for item in fields(ValuePatch)} == {"operations"}
    assert {item.name for item in fields(OperationCallState)} == {
        "plan",
        "bound_values",
        "call_cache",
    }


def test_common_case_arbitration_distinguishes_all_outcomes() -> None:
    selected = arbitrate_cases([("one", 1)], [])
    ambiguous = arbitrate_cases([("one", 1), ("two", 2)], [])
    no_matches: list[tuple[str, int]] = []
    malformed_inputs = [("one", Malformed(ValueError("bad")))]
    malformed = arbitrate_cases(no_matches, malformed_inputs)
    no_malformed: list[tuple[str, Malformed]] = []
    missing = arbitrate_cases(no_matches, no_malformed)

    assert selected == SelectedCase("one", 1)
    assert ambiguous == AmbiguousCases(("one", "two"))
    assert isinstance(malformed, MalformedCase)
    assert malformed.case == "one"
    assert isinstance(malformed.malformed.cause, ValueError)
    assert isinstance(missing, NoCaseMatch)


class _ForeignDeclaration:
    pass


class _Contract:
    operation_id = "ws01.http"
    method = "GET"
    path = "/items"
    input_fields: tuple[InputField, ...] = ()
    responses = object()


def test_http_compiler_rejects_a_foreign_nominal_registry() -> None:
    foreign_kind = CompilerKind[_ForeignDeclaration]("websocket")
    foreign_registry = CompilerRegistry[_ForeignDeclaration, object](foreign_kind)

    with pytest.raises(PlanError, match="HTTP compiler received 'websocket' registry"):
        compile_endpoint(
            _Contract(),
            registry=cast(Any, foreign_registry),
        )


def test_strict_mypy_rejects_mixed_declaration_registries(tmp_path: Path) -> None:
    fixture = tmp_path / "registry_mixing.py"
    fixture.write_text(
        """\
from eazy_sdk.core.kernel import CompilerKind, CompilerRegistry

class HttpDeclaration: ...
class WsDeclaration: ...

http_kind = CompilerKind[HttpDeclaration]("http")
ws_kind = CompilerKind[WsDeclaration]("websocket")
http_registry = CompilerRegistry[HttpDeclaration, int](http_kind)
ws_registry = CompilerRegistry[WsDeclaration, int](ws_kind)

def compile_http(registry: CompilerRegistry[HttpDeclaration, int]) -> None:
    pass

compile_http(http_registry)
compile_http(ws_registry)
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "incompatible type" in result.stdout.lower()
    assert "CompilerRegistry[WsDeclaration, int]" in result.stdout


def test_http_declaration_module_is_explicit_and_not_universal() -> None:
    internal = ROOT / "eazy_sdk" / "compile"

    assert (internal / "http_operation.py").is_file()
    assert not (internal / "operation.py").exists()
    imported = _imports(internal / "http_operation.py")
    assert "eazy_sdk.websocket" not in imported
    assert "zapros.websocket" not in imported


def test_request_location_remains_http_only() -> None:
    assert RequestLocation.HEADER.value == "header"
    assert not hasattr(ValueSlot, "location")
    assert not hasattr(ValueSlot, "wire_name")
