"""Install every published extra from built wheels into a fresh venv and exercise it.

Catches the "works from the workspace, breaks from the wheel" class: a plugin whose
runtime import is not covered by the dependencies its distribution declares.

Usage: ``uv run python scripts/extras_smoke.py <dist-dir> [--only html,accounts]``
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# extra/distribution label -> (requirement template, python -c program)
CHECKS: dict[str, tuple[str, str]] = {
    "core": (
        "eazy-sdk=={version}",
        "import eazy_sdk; from eazy_sdk import Client; "
        "Client(base_url='https://example.test').close()",
    ),
    "html": (
        "eazy-sdk[html]=={version}",
        "from dataclasses import dataclass; from typing import Annotated\n"
        "from eazy_sdk_html import CSS, parse_html\n"
        "@dataclass\n"
        "class Page:\n"
        "    title: Annotated[str, CSS('h1::text')]\n"
        "assert parse_html('<h1>Hi</h1>', Page).title == 'Hi'",
    ),
    "accounts": (
        "eazy-sdk[accounts]=={version}",
        "import eazy_sdk_accounts, eazy_sdk_accounts.storage, eazy_sdk_accounts.http",
    ),
    "sqlmodel": (
        "eazy-sdk[sqlmodel]=={version}",
        "import eazy_sdk_sqlmodel",
    ),
    "httpx": (
        "eazy-sdk[httpx]=={version}",
        "from eazy_sdk import Client; Client.httpx(base_url='https://example.test').close()",
    ),
    "requests": (
        "eazy-sdk[requests]=={version}",
        "from eazy_sdk import Client; Client.requests(base_url='https://example.test').close()",
    ),
    "curl-cffi": (
        "eazy-sdk[curl-cffi]=={version}",
        "from eazy_sdk import Client; Client.curl_cffi(base_url='https://example.test').close()",
    ),
    "websocket": (
        "eazy-sdk[websocket]=={version}",
        "import eazy_sdk.websocket",
    ),
    "pydantic": (
        "eazy-sdk[pydantic]=={version}",
        "import pydantic; from eazy_sdk.models import default_model_adapters; "
        "default_model_adapters()",
    ),
    "msgspec": (
        "eazy-sdk[msgspec]=={version}",
        "import msgspec; import eazy_sdk.models",
    ),
    "presets": (
        "eazy-sdk-presets=={version}",
        "import eazy_sdk_presets",
    ),
    "openapi": (
        "eazy-sdk-openapi=={version}",
        "import eazy_sdk_openapi, eazy_sdk_openapi.generator, eazy_sdk_openapi.cli",
    ),
    "asyncapi": (
        "eazy-sdk-asyncapi=={version}",
        "import eazy_sdk_asyncapi, eazy_sdk_asyncapi.generator, eazy_sdk_asyncapi.cli",
    ),
    "xml": (
        "eazy-sdk-xml=={version}",
        "import eazy_sdk_xml",
    ),
}


def _version() -> str:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _venv_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def smoke(dist: Path, only: set[str] | None, keep: bool) -> list[str]:
    version = _version()
    failures: list[str] = []
    workspace = Path(tempfile.mkdtemp(prefix="eazy-extras-", dir=REPOSITORY_ROOT / ".test-tmp"))
    try:
        for label, (requirement, program) in CHECKS.items():
            if only is not None and label not in only:
                continue
            venv = workspace / label
            created = _run(["uv", "venv", "--quiet", "--python", sys.executable, str(venv)])
            if created.returncode != 0:
                failures.append(f"{label}: venv creation failed\n{created.stderr}")
                continue
            installed = _run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--quiet",
                    "--python",
                    str(_venv_python(venv)),
                    "--find-links",
                    str(dist),
                    requirement.format(version=version),
                ]
            )
            if installed.returncode != 0:
                failures.append(f"{label}: install failed\n{installed.stderr}")
                continue
            ran = _run([str(_venv_python(venv)), "-I", "-c", program])
            if ran.returncode != 0:
                failures.append(f"{label}: smoke program failed\n{ran.stderr}")
                continue
            print(f"ok: {requirement.format(version=version)}")
    finally:
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path, help="directory with all workspace wheels")
    parser.add_argument("--only", help="comma-separated subset of extras to check")
    parser.add_argument("--keep", action="store_true", help="keep the temporary venvs")
    args = parser.parse_args()
    (REPOSITORY_ROOT / ".test-tmp").mkdir(exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    unknown = (only or set()) - set(CHECKS)
    if unknown:
        parser.error(f"unknown extras: {sorted(unknown)}")
    failures = smoke(args.dist.resolve(), only, args.keep)
    if failures:
        print("\n\n".join(failures), file=sys.stderr)
        return 1
    print(f"OK: {len(only or CHECKS)} extras install from wheels and import cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
