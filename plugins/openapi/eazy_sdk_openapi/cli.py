"""Command-line entry point for ``eazy-sdk-openapi``."""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from eazy_sdk_openapi.generator import GenerationConfig, ProjectionImport, generate_package


def load_document(path: Path) -> Mapping[str, Any]:
    """Load an OpenAPI JSON document, or YAML when PyYAML is installed."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        value = json.loads(text)
    else:
        try:
            yaml = importlib.import_module("yaml")
        except ImportError as exc:
            raise RuntimeError(
                "YAML input requires PyYAML; install eazy-sdk-openapi[yaml]"
            ) from exc
        value = yaml.safe_load(text)
    if not isinstance(value, Mapping):
        raise ValueError("OpenAPI document root must be an object")
    return cast(Mapping[str, Any], value)


def projection_import(value: str) -> ProjectionImport:
    requirement, separator, implementation = value.partition("=")
    if not separator or not requirement or not implementation:
        raise argparse.ArgumentTypeError(
            "projection must use REQUIREMENT=MODULE:ATTRIBUTE"
        )
    try:
        return ProjectionImport(requirement, implementation)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def main(argv: Sequence[str] | None = None) -> int:
    """Generate an importable SDK package and return a process exit status."""
    parser = argparse.ArgumentParser(prog="eazy-sdk-openapi")
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--package-name", required=True)
    parser.add_argument(
        "--projection",
        action="append",
        default=[],
        type=projection_import,
        metavar="REQUIREMENT=MODULE:ATTRIBUTE",
    )
    args = parser.parse_args(argv)
    document = load_document(args.spec)
    generated = generate_package(
        document,
        spec_path=args.spec,
        output_directory=args.output,
        package_name=args.package_name,
        config=GenerationConfig(tuple(args.projection)),
    )
    print(generated)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
