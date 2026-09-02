from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


POSITIVE = r'''# pyright: strict
from dataclasses import dataclass
from typing import assert_type

from eazy_sdk import ClientConfig
from eazy_sdk.protection import (
    ChallengeSolver,
    InstallableProtection,
    SolutionFields,
    SolveContext,
    challenge_guard,
    safe_method,
    solution_fields,
)
from eazy_sdk.ext import ParsedValue, RequestScope
from eazy_sdk.protection.advanced import (
    ChallengePolicySpec,
    PrivateBindings,
    ResponseSignal,
    SolverRequirement,
    challenge_policy,
    per_match,
    private_bindings,
    private_cookie,
)
from eazy_sdk.response import ResponseContext
from eazy_sdk.response import callable_parser
from eazy_sdk_presets import cloudflare, host


@dataclass(frozen=True)
class Challenge:
    revision: int


@dataclass(frozen=True)
class Clearance:
    token: str


class Solver:
    async def solve(
        self,
        challenge: Challenge,
        context: SolveContext,
    ) -> Clearance:
        return Clearance(f"token-{challenge.revision}-{context.attempt}")


def detect(context: ResponseContext[object]) -> Challenge | None:
    return Challenge(1) if context.response.status_code == 403 else None


solver: ChallengeSolver[Challenge, Clearance] = Solver()
application: SolutionFields[Clearance] = solution_fields(
    cookies={"clearance": "token"},
)
guard = challenge_guard(
    name="typed.custom",
    scope=host("api.example"),
    detect=detect,
    solver=solver,
    apply=application,
    replay=safe_method(),
)
config = ClientConfig().with_protection(guard)
first_party = ClientConfig().with_protection(
    cloudflare.challenge_pages(scope=host("api.example"), solver=None),
)

assert_type(guard, InstallableProtection)
assert_type(config, ClientConfig)
assert_type(first_party, ClientConfig)

advanced_scope = RequestScope(operation_ids=frozenset({"typed.advanced"}))
advanced_requirement = SolverRequirement[Challenge, Clearance]("typed.advanced")


def parse_advanced(context: ResponseContext[object]) -> ParsedValue[Challenge]:
    return ParsedValue(Challenge(context.attempt.number))


advanced_signal = ResponseSignal(
    "typed.advanced",
    advanced_scope,
    Challenge,
    callable_parser(Challenge, parse_advanced),
)
advanced_application: PrivateBindings[Clearance] = private_bindings(
    private_cookie("clearance", field="token"),
)
advanced_policy = challenge_policy(
    scope=advanced_scope,
    signal=advanced_signal,
    solver=advanced_requirement,
    apply=advanced_application,
    persistence=per_match(),
    replay=safe_method(),
)
assert_type(advanced_policy, ChallengePolicySpec[Challenge, Clearance])
'''


NEGATIVE = r'''# pyright: strict
from eazy_sdk import ClientConfig
from eazy_sdk.protection import SolveContext
from eazy_sdk.protection import ResponseSignal
from eazy_sdk_presets import cloudflare, host


class BadSolver:
    async def solve(
        self,
        challenge: cloudflare.CloudflareChallenge,
        context: SolveContext,
    ) -> str:
        return "not-a-clearance"


guard = cloudflare.challenge_pages(scope=host("api.example"), solver=BadSolver())
config = ClientConfig().with_protection(object())
'''


def _run(checker: str, source: Path) -> subprocess.CompletedProcess[str]:
    command = (
        [sys.executable, "-m", "mypy", "--strict", str(source)]
        if checker == "mypy"
        else [
            sys.executable,
            "-m",
            "basedpyright",
            "--pythonversion",
            "3.13",
            "--level",
            "error",
            str(source),
        ]
    )
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_phase29_high_level_guard_and_preset_types_are_preserved(checker: str) -> None:
    with tempfile.TemporaryDirectory(prefix="phase28-typing-", dir=ROOT / "tests") as temp:
        source = Path(temp) / "positive.py"
        source.write_text(POSITIVE, encoding="utf-8")
        result = _run(checker, source)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_phase29_typing_rejects_invalid_solver_and_installable(checker: str) -> None:
    with tempfile.TemporaryDirectory(prefix="phase28-typing-", dir=ROOT / "tests") as temp:
        source = Path(temp) / "negative.py"
        source.write_text(NEGATIVE, encoding="utf-8")
        result = _run(checker, source)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "BadSolver" in output
    assert "with_protection" in output
