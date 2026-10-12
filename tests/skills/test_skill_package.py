"""The skill as a package: its page, its references, its scaffold."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import refs_freshness
import sdk_replay
import sdk_scaffold

SKILL = Path(__file__).resolve().parents[2] / "skills" / "eazy-sdk-from-capture"


def test_skill_page_has_a_name_and_a_description_that_says_when() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    assert "name: eazy-sdk-from-capture" in front
    description = re.search(r"description: (.+)", front)
    assert description is not None
    for trigger in ("HAR", "Burp", "recorded traffic", "record a browser session"):
        assert trigger in description.group(1)


def test_every_file_the_skill_page_names_exists() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`?((?:references|scripts)/[\w.-]+\.(?:md|py))`?", text))
    named |= {f"scripts/{name}" for name in re.findall(r"\| `(\w+\.py)` \|", text)}
    assert len(named) >= 14
    missing = sorted(name for name in named if not (SKILL / name).exists())
    assert missing == []


def test_every_reference_page_is_named_by_the_skill_page() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    pages = sorted(path.name for path in (SKILL / "references").glob("*.md"))
    assert pages
    assert [name for name in pages if f"references/{name}" not in text] == []


def test_references_import_only_what_the_library_has() -> None:
    assert refs_freshness.check(SKILL / "references") == []


def test_a_stale_reference_is_reported(tmp_path: Path) -> None:
    (tmp_path / "page.md").write_text(
        "# Page\n\n```python\nfrom eazy_sdk import Http, NoSuchThing\n"
        "from eazy_sdk.nowhere import x\nimport json\n```\n\n"
        "```python\nfrom eazy_sdk.response import Location\nclass Broken(:\n```\n",
        encoding="utf-8",
    )
    problems = refs_freshness.check(tmp_path)
    assert problems == [
        "page.md:4: eazy_sdk has no NoSuchThing",
        "page.md:5: module eazy_sdk.nowhere does not import: No module named 'eazy_sdk.nowhere'",
    ]


def test_scaffold_writes_a_project_the_replay_can_be_pointed_at(tmp_path: Path) -> None:
    created, kept = sdk_scaffold.scaffold("site_sdk", tmp_path)
    assert kept == []
    names = {path.relative_to(tmp_path).as_posix() for path in created}
    assert {
        "pyproject.toml",
        "site_sdk/__init__.py",
        "site_sdk/hosts.py",
        "site_sdk/http/operations.py",
        "site_sdk/auth/steps.py",
        "tests/replay/factory.py",
        "tests/replay/scenarios.toml",
        "examples/live_check.py",
    } <= names
    project = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["name"] == "site-sdk"
    assert project["project"]["dependencies"][0].startswith(
        "eazy-sdk-core[curl-cffi,pydantic,html]>="
    )
    ignored = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".sdk-work/" in ignored
    assert "captures/" in ignored
    scenarios = tomllib.loads(
        (tmp_path / "tests/replay/scenarios.toml").read_text(encoding="utf-8")
    )
    assert scenarios["sdk"] == {"factory": "tests.replay.factory:open_sdk", "mode": "async"}
    # The template has no scenario yet: the replay says so instead of passing on nothing.
    try:
        sdk_replay.load_plan(tmp_path / "tests/replay/scenarios.toml")
    except sdk_replay.ScenarioError as error:
        assert "no [[scenario]]" in str(error)
    else:  # pragma: no cover
        raise AssertionError("an empty scenarios file must not load")


def test_scaffold_never_overwrites(tmp_path: Path) -> None:
    sdk_scaffold.scaffold("site_sdk", tmp_path)
    module = tmp_path / "site_sdk" / "hosts.py"
    module.write_text("# mine\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("captures/\nmine\n", encoding="utf-8")
    created, kept = sdk_scaffold.scaffold("site_sdk", tmp_path)
    assert created == []
    assert module in kept
    assert module.read_text(encoding="utf-8") == "# mine\n"
    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["captures/", "mine"]
    assert lines.count("captures/") == 1


def test_scaffold_refuses_a_name_that_cannot_be_imported(tmp_path: Path) -> None:
    for name in ("site-sdk", "Site", "1sdk"):
        try:
            sdk_scaffold.scaffold(name, tmp_path)
        except ValueError as error:
            assert "importable" in str(error)
        else:  # pragma: no cover
            raise AssertionError(name)
