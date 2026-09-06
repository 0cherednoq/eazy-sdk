"""Phase 50 absence tests: one execution path, and the removed names stay removed."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import eazy_sdk
import eazy_sdk.api as api_module
import eazy_sdk.compile.input as input_module
from eazy_sdk.request import Wire

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_IN_RUNTIME = (
    r"\.accepts_options\b",
    r"\.is_decorated\b",
    r"\.synthesized\b",
    r"descriptor\.signature\b",
    r"\bUnpack\b",
)
"""What the compiler and the executor must never read: whether a decorator wrote the class."""


def _sources(*packages: str) -> list[Path]:
    return [
        path
        for package in packages
        for path in (ROOT / "eazy_sdk" / package).rglob("*.py")
    ]


def test_no_second_execution_path() -> None:
    """I2: the compiler and the executor never ask who wrote the operation class."""

    pattern = re.compile("|".join(FORBIDDEN_IN_RUNTIME))
    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in _sources("compile", "clients")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []
    assert not hasattr(input_module, "inspect_method_input")
    assert not hasattr(input_module, "_append_unpacked_fields")
    parameters = inspect.signature(input_module.inspect_operation_input).parameters
    assert "accepts_options" not in parameters


def test_removed_names_are_gone() -> None:
    """The alpha keeps no aliases: the old spellings do not exist."""

    assert not hasattr(api_module, "_SingularOperationDecorator")
    assert not hasattr(api_module, "_AsyncOperationDescriptor")
    assert not hasattr(api_module, "_SyncOperationDescriptor")
    assert not hasattr(api_module, "_singular_responses")
    assert "projection" not in Wire.__dataclass_fields__
    assert "unpacked" not in input_module.MethodInputSchema.__dataclass_fields__
    for name in ("Responses", "Success", "Error", "Text", "Bytes"):
        assert name not in eazy_sdk.__all__
