from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


POSITIVE = r'''# pyright: strict
from typing import assert_type

from eazy_sdk import ClientConfig
from eazy_sdk.protection import (
    ChallengeSolver,
    NetworkIdentity,
    NetworkIdentityContext,
    NetworkIdentityProvider,
    ProtectionBundle,
    ProtectionCapabilities,
    SolveContext,
)
from eazy_sdk_presets import cloudflare, host


class Provider:
    def current(self, context: NetworkIdentityContext) -> NetworkIdentity:
        return NetworkIdentity(proxy=f"proxy-{context.attempt}")


class Solver:
    capabilities = ProtectionCapabilities(
        javascript=True,
        browser=True,
    )

    async def solve(
        self,
        challenge: cloudflare.CloudflareChallenge,
        context: SolveContext,
    ) -> cloudflare.CloudflareClearance:
        return cloudflare.CloudflareClearance(
            (cloudflare.SecretCookie("cf_clearance", "secret"),),
            expected_identity=context.network_identity,
        )


provider: NetworkIdentityProvider = Provider()
solver: ChallengeSolver[
    cloudflare.CloudflareChallenge,
    cloudflare.CloudflareClearance,
] = Solver()
guard = cloudflare.challenge_pages(scope=host("api.example"), solver=solver)
bundle = guard.to_bundle()
config = ClientConfig(network_identity=provider).with_protection(guard)

assert_type(bundle, ProtectionBundle)
assert_type(config, ClientConfig)
'''


NEGATIVE = r'''# pyright: strict
from eazy_sdk import ClientConfig
from eazy_sdk.protection import NetworkIdentityContext, NetworkIdentityProvider, SolveContext
from eazy_sdk_presets import cloudflare, host


class BadProvider:
    def current(self, context: NetworkIdentityContext) -> str:
        return "not-an-identity"


class BadSolver:
    async def solve(
        self,
        challenge: cloudflare.CloudflareChallenge,
        context: SolveContext,
    ) -> str:
        return "not-a-clearance"


provider: NetworkIdentityProvider = BadProvider()
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
def test_phase28_identity_solver_and_bundle_types_are_preserved(checker: str) -> None:
    with tempfile.TemporaryDirectory(prefix="phase28-typing-", dir=ROOT / "tests") as temp:
        source = Path(temp) / "positive.py"
        source.write_text(POSITIVE, encoding="utf-8")
        result = _run(checker, source)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_phase28_typing_rejects_invalid_provider_solver_and_bundle(checker: str) -> None:
    with tempfile.TemporaryDirectory(prefix="phase28-typing-", dir=ROOT / "tests") as temp:
        source = Path(temp) / "negative.py"
        source.write_text(NEGATIVE, encoding="utf-8")
        result = _run(checker, source)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "BadProvider" in output
    assert "BadSolver" in output
    assert "with_protection" in output
