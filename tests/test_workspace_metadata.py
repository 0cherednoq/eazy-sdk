"""Source-completeness checks for every workspace distribution."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
WORKSPACE_PROJECTS = (
    REPOSITORY,
    REPOSITORY / "plugins" / "asyncapi",
    REPOSITORY / "plugins" / "openapi",
    REPOSITORY / "plugins" / "presets",
    REPOSITORY / "plugins" / "sqlmodel",
    REPOSITORY / "plugins" / "xml",
)


@pytest.mark.parametrize("project", WORKSPACE_PROJECTS, ids=lambda path: path.name)
def test_project_readme_is_present_in_source(project: Path) -> None:
    with (project / "pyproject.toml").open("rb") as stream:
        readme = tomllib.load(stream)["project"]["readme"]

    path = project / readme
    assert path.is_file(), f"workspace metadata references a missing readme: {path}"
    assert path.read_text(encoding="utf-8").startswith("# ")
