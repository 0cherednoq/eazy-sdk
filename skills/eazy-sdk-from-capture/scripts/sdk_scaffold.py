"""Create the skeleton of an SDK project.

    python sdk_scaffold.py --package site_sdk --root path/to/project

Writes ``pyproject.toml``, the package with the modules of ``references/layout.md``, the two
replay files and the ignore rules. Existing files are never overwritten: the script reports
them and leaves them alone.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path

_FALLBACK_VERSION = "0.2.0a15"
_IGNORED = (".sdk-work/", "captures/", "__pycache__/", ".venv/")

_MODULES: dict[str, str] = {
    "__init__.py": '"""{title}: a declarative SDK on eazy-sdk."""\n\n__all__: list[str] = []\n',
    "config.py": '"""Addresses and transport settings of one instance of the site."""\n',
    "hosts.py": '"""One router ancestor per host of the site."""\n',
    "exceptions.py": '"""The exception hierarchy of the SDK."""\n',
    "security.py": '"""The session scheme."""\n',
    "models/__init__.py": '"""Public result models."""\n',
    "http/__init__.py": '"""Operations and routers of the API."""\n',
    "http/operations.py": '"""Operations of the API."""\n',
    "http/routers.py": '"""Routers, the root SDK and how it is opened."""\n',
    "auth/__init__.py": '"""Sign-in: steps and the service that runs them."""\n',
    "auth/steps.py": '"""Sign-in steps as operations."""\n',
    "auth/service.py": '"""The service that runs the sign-in steps."""\n',
}

_PYPROJECT = """\
[project]
name = "{distribution}"
version = "0.1.0"
description = "A declarative SDK on eazy-sdk."
requires-python = ">=3.13"
dependencies = [
    "eazy-sdk-core[curl-cffi,pydantic,html]>={version}",
    "curl_cffi>=0.7",
    "pydantic>=2",
]

[dependency-groups]
dev = ["mypy>=1.11", "ruff>=0.6"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["{package}"]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]

[tool.ruff]
line-length = 100
"""

_FACTORY = '''\
"""How the replay opens the SDK: the transport is the handler it is given."""

from typing import Any


def open_sdk(handler: Any, **options: Any) -> Any:
    """Return the SDK opened on ``handler``; ``options`` come from the scenario."""
    raise NotImplementedError("open the root SDK of {package} on the given handler")
'''

_SCENARIOS = """\
# What the replay runs. See references/replay.md of the skill.

[sdk]
factory = "tests.replay.factory:open_sdk"
mode = "async"

# [[scenario]]
# name = "sign in"
# exchanges = "1-4"
# call = "mail.list"
# [scenario.options]
# username = "__SECRET_email_1__"
# password = "__SECRET_password_1__"
# [scenario.expect]
# returns = "ThreadsPage"

# [[exclude]]
# exchanges = "5"
# reason = "why this exchange is not part of the SDK"
"""


def installed_version() -> str:
    """Return the installed ``eazy-sdk-core`` version, or the oldest one the skill knows."""
    try:
        return metadata.version("eazy-sdk-core")
    except metadata.PackageNotFoundError:
        return _FALLBACK_VERSION


def plan(package: str, root: Path) -> dict[Path, str]:
    """Return every file of the skeleton with its content."""
    title = package.replace("_", "-")
    files = {root / package / name: text.format(title=title) for name, text in _MODULES.items()}
    files[root / "pyproject.toml"] = _PYPROJECT.format(
        distribution=title, package=package, version=installed_version()
    )
    files[root / "tests" / "__init__.py"] = ""
    files[root / "tests" / "replay" / "__init__.py"] = ""
    files[root / "tests" / "replay" / "factory.py"] = _FACTORY.format(package=package)
    files[root / "tests" / "replay" / "scenarios.toml"] = _SCENARIOS
    files[root / "examples" / "live_check.py"] = (
        '"""A run against the live site. Needs real credentials; not part of the gates."""\n'
    )
    return files


def ignore(root: Path) -> list[str]:
    """Make sure recordings and work files are ignored; return the lines that were added."""
    path = root / ".gitignore"
    present = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    added = [line for line in _IGNORED if line not in present]
    if added:
        path.write_text("\n".join([*present, *added]) + "\n", encoding="utf-8")
    return added


def scaffold(package: str, root: Path) -> tuple[list[Path], list[Path]]:
    """Write the skeleton; return the files created and the files left as they were."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", package):
        raise ValueError(f"{package!r} is not an importable package name")
    created: list[Path] = []
    kept: list[Path] = []
    for path, text in plan(package, root).items():
        if path.exists():
            kept.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        created.append(path)
    ignore(root)
    return created, kept


def main(argv: Sequence[str] | None = None) -> int:
    """Create the skeleton of an SDK project."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package", required=True, help="import name, e.g. site_sdk")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        created, kept = scaffold(args.package, args.root)
    except ValueError as error:
        parser.error(str(error))
    print(f"created {len(created)} files under {args.root}")
    for path in kept:
        print(f"left as it was: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
