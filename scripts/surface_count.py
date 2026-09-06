"""Count the distinct public names an SDK author can import — the name-budget gate.

Phase 50 promised no net growth of the public surface: every module an author imports from
is walked, its ``__all__`` collected, and the *distinct* objects behind those names counted.
One object exported under one name from two modules is one name; ``eazy_sdk.Query`` and
``eazy_sdk.request.markers.Query`` are two, because they are two different things.

Usage::

    uv run python scripts/surface_count.py            # table + total
    uv run python scripts/surface_count.py --total    # the number only
    uv run python scripts/surface_count.py --max N    # exit 1 when the total exceeds N
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

AUTHOR_MODULES: tuple[str, ...] = (
    "eazy_sdk",
    "eazy_sdk.request",
    "eazy_sdk.request.markers",
    "eazy_sdk.response",
    "eazy_sdk.models",
    "eazy_sdk.codecs",
    "eazy_sdk.crypto",
    "eazy_sdk.protection",
    "eazy_sdk.protection.advanced",
    "eazy_sdk.websocket",
    "eazy_sdk.protocols",
    "eazy_sdk.dependencies",
    "eazy_sdk.serialization",
    "eazy_sdk.clients",
    "eazy_sdk.handlers",
    "eazy_sdk.auth",
    "eazy_sdk.identity",
    "eazy_sdk.root",
    "eazy_sdk.sentinels",
    "eazy_sdk.operation",
    "eazy_sdk.ext",
)
"""Every module the authoring reference tells an SDK author to import from."""


def surface() -> tuple[dict[str, int], int]:
    """Per-module ``__all__`` sizes and the number of distinct exported objects."""

    per_module: dict[str, int] = {}
    seen: set[int] = set()
    for module_name in AUTHOR_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            per_module[module_name] = 0
            continue
        names = tuple(getattr(module, "__all__", ()))
        per_module[module_name] = len(names)
        for name in names:
            value = getattr(module, name)
            seen.add(id(value))
    return per_module, len(seen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--total", action="store_true", help="print the total only")
    parser.add_argument("--max", type=int, default=None, help="fail when the total exceeds N")
    arguments = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    per_module, total = surface()
    if arguments.total:
        print(total)
    else:
        width = max(len(name) for name in per_module)
        for name, count in per_module.items():
            print(f"{name:<{width}}  {count:>4}")
        print(f"{'distinct public names':<{width}}  {total:>4}")
    if arguments.max is not None and total > arguments.max:
        print(f"surface budget exceeded: {total} > {arguments.max}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
