from __future__ import annotations

import tomllib
from pathlib import Path

from eazy_sdk_openapi.generator import (
    render_auth,
    render_client,
    render_dependencies,
    render_model_base,
    render_signatures,
)
from eazy_sdk_openapi.ir import parse_openapi

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = (
    ROOT / "pyproject.toml",
    ROOT / "plugins" / "asyncapi" / "pyproject.toml",
    ROOT / "plugins" / "openapi" / "pyproject.toml",
    ROOT / "plugins" / "presets" / "pyproject.toml",
    ROOT / "plugins" / "sqlmodel" / "pyproject.toml",
    ROOT / "plugins" / "xml" / "pyproject.toml",
)


def test_all_distributions_share_the_verified_python_policy() -> None:
    for path in PROJECTS:
        project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        assert project["requires-python"] == ">=3.13", path
        assert "Programming Language :: Python :: 3.13" in project["classifiers"], path
        assert "Programming Language :: Python :: 3.14" in project["classifiers"], path

    root = tomllib.loads(PROJECTS[0].read_text(encoding="utf-8"))
    assert root["tool"]["ruff"]["target-version"] == "py313"
    assert root["tool"]["mypy"]["python_version"] == "3.13"
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.13"
    assert 'python: "3.13"' in (ROOT / ".readthedocs.yaml").read_text(encoding="utf-8")


def test_generated_openapi_modules_defer_annotations_on_python_313() -> None:
    ir = parse_openapi(
        {
            "openapi": "3.1.1",
            "info": {"title": "phase26", "version": "1"},
            "paths": {},
        }
    )
    modules = (
        render_client(ir),
        render_auth(ir),
        render_dependencies(ir),
        render_signatures(ir),
        render_model_base(),
    )
    for source in modules:
        assert "from __future__ import annotations" in source
        compile(source, "generated.py", "exec")
