"""Run every gate an SDK written from a capture has to pass.

    python sdk_check.py WORKDIR --package PACKAGE --scenarios tests/replay/scenarios.toml

Run from the root of the SDK project, in its environment. One command, one report
(``WORKDIR/check-report.json``), exit code 0 only when every gate passed. A gate that could
not run is reported as skipped with its reason and is not a pass.

    G1  the package and every module in it import
    G2  mypy --strict over the package
    G3  ruff check over the package
    G4  every replay scenario ends as expected
    G5  the SDK makes no request the capture does not have
    G6  every recorded exchange is used by a scenario or excluded with a reason
    G7  every request of the SDK matches the recorded one
    G8  no secret of the capture is anywhere in the project
    G9  nothing is imported from private modules of eazy_sdk
    G10 the skill's references match the installed library
"""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import json
import pkgutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import refs_freshness
import sdk_replay
from capture_model import load
from capture_redact import occurrence

REPORT_FORMAT = "eazy-check/1"
PASS, FAIL, SKIP = "pass", "fail", "skipped"

_SKIPPED_DIRECTORIES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".idea", ".vscode", "dist", "build", "captures", ".tox",
}  # fmt: skip
_GUARDED_PACKAGES = ("eazy_sdk", "zapros")
_MIN_SECRET = 6
_MAX_SCANNED_BYTES = 2_000_000
_TOOL_TIMEOUT = 600


def gate(status: str, details: Sequence[str] = ()) -> dict[str, Any]:
    """Build one gate result."""
    return {"status": status, "details": list(details)[:50]}


# --- G1 -----------------------------------------------------------------------------------


def check_imports(package: str) -> dict[str, Any]:
    """Import the package and every module in it."""
    failures: list[str] = []
    try:
        root = importlib.import_module(package)
    except Exception as error:  # noqa: BLE001 - any import failure is the finding
        return gate(FAIL, [f"{package}: {type(error).__name__}: {error}"])
    names = [package]
    for info in pkgutil.walk_packages(getattr(root, "__path__", []), prefix=f"{package}."):
        names.append(info.name)
        try:
            importlib.import_module(info.name)
        except Exception as error:  # noqa: BLE001
            failures.append(f"{info.name}: {type(error).__name__}: {error}")
    if failures:
        return gate(FAIL, failures)
    return gate(PASS, [f"{len(names)} modules import; operations compile on their first call"])


# --- G2, G3 -------------------------------------------------------------------------------


def run_tool(module: str, arguments: Sequence[str]) -> dict[str, Any]:
    """Run a checker installed in the SDK's environment."""
    if importlib.util.find_spec(module) is None:
        return gate(SKIP, [f"{module} is not installed in this environment"])
    try:
        done = subprocess.run(
            [sys.executable, "-m", module, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TOOL_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return gate(FAIL, [f"{module} did not finish in {_TOOL_TIMEOUT} seconds"])
    lines = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    return gate(PASS if done.returncode == 0 else FAIL, lines[-40:])


# --- G8 -----------------------------------------------------------------------------------


def project_files(root: Path, skip: Sequence[Path]) -> Iterator[Path]:
    """Yield the files of a project that belong to it, not to its tooling or to a capture."""
    skipped = {path.resolve() for path in skip}
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            if entry.is_dir():
                if entry.name not in _SKIPPED_DIRECTORIES and entry.resolve() not in skipped:
                    stack.append(entry)
            elif entry.name == ".env" or entry.name.startswith(".env."):
                # Where a person keeps credentials for a live run: theirs, and never committed.
                continue
            elif entry.is_file() and entry.stat().st_size <= _MAX_SCANNED_BYTES:
                yield entry


def check_secrets(root: Path, secrets: Mapping[str, str], skip: Sequence[Path]) -> dict[str, Any]:
    """Look for any secret value of the capture in the project's files."""
    if not secrets:
        return gate(SKIP, ["no secrets.json in the work directory: the capture was not redacted"])
    # The same notion of an occurrence as the redaction has: a short value counts only as a
    # whole word, so `support` is not found inside `supported_fields`.
    wanted = {
        name: occurrence(value) for name, value in secrets.items() if len(value) >= _MIN_SECRET
    }
    found: list[str] = []
    for path in project_files(root, skip):
        data = path.read_bytes()
        for name, pattern in wanted.items():
            match = pattern.search(data)
            if match is not None:
                line = data.count(b"\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(root).as_posix()}:{line}: {name}")
    return gate(FAIL if found else PASS, found or [f"{len(wanted)} secret values, none found"])


# --- G9 -----------------------------------------------------------------------------------


def _private(module: str) -> bool:
    parts = module.split(".")
    return parts[0] in _GUARDED_PACKAGES and any(part.startswith("_") for part in parts[1:])


def private_imports(source: str) -> list[tuple[int, str]]:
    """List imports of private modules or names of the library in one source file."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names if _private(alias.name)]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _private(node.module):
                found.append((node.lineno, node.module))
            elif node.module.split(".")[0] in _GUARDED_PACKAGES:
                found += [
                    (node.lineno, f"{node.module}.{alias.name}")
                    for alias in node.names
                    if alias.name.startswith("_")
                ]
    return found


def check_private_imports(root: Path, skip: Sequence[Path]) -> dict[str, Any]:
    """Refuse imports from the private modules of the library."""
    found: list[str] = []
    count = 0
    for path in project_files(root, skip):
        if path.suffix != ".py":
            continue
        count += 1
        try:
            hits = private_imports(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            found.append(f"{path.relative_to(root).as_posix()}: does not parse: {error.msg}")
            continue
        found += [f"{path.relative_to(root).as_posix()}:{line}: {name}" for line, name in hits]
    return gate(FAIL if found else PASS, found or [f"{count} files, no private import"])


# --- G10 ----------------------------------------------------------------------------------


def check_references() -> dict[str, Any]:
    """Compare the skill's references with the installed library."""
    references = Path(__file__).resolve().parents[1] / "references"
    if not any(references.glob("*.md")):
        return gate(SKIP, [f"no reference pages in {references}"])
    problems = refs_freshness.check(references)
    return gate(FAIL if problems else PASS, problems)


# --- all ----------------------------------------------------------------------------------


def run(
    workdir: Path,
    package: str,
    scenarios: Path,
    root: Path,
    *,
    tools: bool = True,
    ignore: Sequence[Path] = (),
) -> dict[str, Any]:
    """Run every gate and collect the report."""
    secrets = sdk_replay.load_secrets(workdir)
    skip = [workdir, *ignore]
    gates: dict[str, dict[str, Any]] = {"G1 package imports": check_imports(package)}
    target = str(Path(*package.split(".")))
    if tools:
        gates["G2 mypy --strict"] = run_tool("mypy", ["--strict", target])
        gates["G3 ruff check"] = run_tool("ruff", ["check", target])
    else:
        gates["G2 mypy --strict"] = gate(SKIP, ["--no-tools"])
        gates["G3 ruff check"] = gate(SKIP, ["--no-tools"])
    replay: dict[str, Any] | None = None
    try:
        plan = sdk_replay.load_plan(scenarios)
        replay = sdk_replay.run(
            plan, load(workdir / "capture.json"), secrets, sdk_replay.load_noise(workdir)
        )
    except (sdk_replay.ScenarioError, OSError) as error:
        failed = gate(FAIL, [f"replay did not run: {error}"])
        gates |= {f"G{number} replay": failed for number in (4, 5, 6, 7)}
    else:
        for name, passed in replay["gates"].items():
            gates[name] = gate(PASS if passed else FAIL)
    gates["G8 no secrets in the project"] = check_secrets(root, secrets, skip)
    gates["G9 no private imports"] = check_private_imports(root, skip)
    gates["G10 references are fresh"] = check_references()
    return {
        "format": REPORT_FORMAT,
        "passed": all(item["status"] == PASS for item in gates.values()),
        "gates": gates,
        "replay": replay,
    }


def render(report: Mapping[str, Any]) -> str:
    """Render a check report for the console."""
    lines: list[str] = []
    if report["replay"] is not None:
        lines += [sdk_replay.render(report["replay"]), ""]
    for name, item in report["gates"].items():
        lines.append(f"{item['status'].upper():7} {name}")
        if item["status"] != PASS:
            lines += [f"          {detail}" for detail in item["details"][:15]]
    lines.append("ALL GATES PASSED" if report["passed"] else "NOT PASSED")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Check an SDK project against its capture."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--package", required=True, help="import name of the SDK package")
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="the SDK project root")
    parser.add_argument("--no-tools", action="store_true", help="skip mypy and ruff")
    parser.add_argument(
        "--ignore",
        type=Path,
        action="append",
        default=[],
        help="a directory G8 and G9 do not scan, e.g. where raw recordings are kept",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    sys.path.insert(0, str(root))
    report = run(
        args.workdir,
        args.package,
        args.scenarios,
        root,
        tools=not args.no_tools,
        ignore=[(root / path) for path in args.ignore],
    )
    (args.workdir / "check-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(render(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
