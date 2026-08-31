"""Command-line entry point for ``eazy-sdk-asyncapi``."""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from .generator import generate_package


def load_document(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.casefold() == ".json":
        value = json.loads(text)
    else:
        try:
            yaml = importlib.import_module("yaml")
        except ImportError as exc:
            raise RuntimeError(
                "YAML input requires PyYAML; install eazy-sdk-asyncapi[yaml]"
            ) from exc
        value = yaml.safe_load(text)
    if not isinstance(value, Mapping):
        raise ValueError("AsyncAPI document root must be an object")
    return cast(Mapping[str, Any], value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eazy-sdk-asyncapi")
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--package-name", required=True)
    args = parser.parse_args(argv)
    generated = generate_package(
        load_document(args.spec),
        spec_path=args.spec,
        output_directory=args.output,
        package_name=args.package_name,
    )
    print(generated)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["load_document", "main"]
