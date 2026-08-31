"""Regenerate committed SDK snapshots from pinned real-world OpenAPI fixtures."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from eazy_sdk_openapi.cli import load_document
from eazy_sdk_openapi.generator import generate_package

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "plugins" / "openapi" / "tests" / "fixtures" / "real_world"
SNAPSHOTS = ROOT / "plugins" / "openapi" / "tests" / "snapshots" / "real_world"
SOURCES = {
    "museum_sdk": (
        "museum-openapi.yaml",
        "25861fd6f830d483b92003c9657a7d90fcfce8a427d0ee7bcb8bd4aabd178af2",
    ),
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="eazy-sdk-openapi-") as temporary:
        output = Path(temporary)
        for package_name, (fixture_name, expected_hash) in SOURCES.items():
            source = FIXTURES / fixture_name
            actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise RuntimeError(
                    f"{fixture_name} changed: update its provenance and expected SHA-256 first"
                )
            generated = generate_package(
                load_document(source),
                spec_path=source,
                output_directory=output,
                package_name=package_name,
            )
            destination = SNAPSHOTS / package_name
            destination.mkdir(parents=True, exist_ok=True)
            generated_names = {
                item.name
                for item in generated.iterdir()
                if item.is_file() and item.name != "py.typed"
            }
            for snapshot in destination.glob("*.snap"):
                if snapshot.name.removesuffix(".snap") not in generated_names:
                    snapshot.unlink()
            for generated_file in generated.iterdir():
                if generated_file.is_file() and generated_file.name != "py.typed":
                    snapshot = destination / f"{generated_file.name}.snap"
                    snapshot.write_bytes(generated_file.read_bytes())
            print(f"updated {destination.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
