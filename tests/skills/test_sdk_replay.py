"""Replay of a capture through a real SDK, and the gates built on it."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import capture_inventory
import capture_normalize
import capture_redact
import pytest
import sdk_check
import sdk_replay
from capture_model import load

from tests.skills.conftest import PASSWORD
from tests.skills.minisdk import write_project


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Yield an empty project root that is importable and forgotten afterwards."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.syspath_prepend(str(root))
    yield root
    for name in [n for n in sys.modules if n == "replay_factory" or n.split(".")[0] == "minisdk"]:
        del sys.modules[name]


@pytest.fixture
def work(burp_base64: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """Return a work directory with the capture normalised, redacted and inventoried."""
    path = tmp_path / "work"
    assert capture_normalize.main([str(burp_base64), "--out", str(path)]) == 0
    assert capture_redact.main([str(path)]) == 0
    assert capture_inventory.main([str(path)]) == 0
    capsys.readouterr()
    return path


def _replay(work: Path, scenarios: Path) -> dict[str, Any]:
    return sdk_replay.run(
        sdk_replay.load_plan(scenarios),
        load(work / "capture.json"),
        sdk_replay.load_secrets(work),
        sdk_replay.load_noise(work),
    )


def test_a_matching_sdk_passes_every_replay_gate(project: Path, work: Path) -> None:
    report = _replay(work, write_project(project))
    assert report["passed"] is True, sdk_replay.render(report)
    wrong, first = report["scenarios"]
    assert wrong["outcome"].startswith("InvalidCredentials")
    assert [item["exchange"] for item in wrong["requests"]] == [1, 2]
    assert first["outcome"] == "returned ThreadsPage"
    assert [item["exchange"] for item in first["requests"]] == [1, 3, 4, 5]
    assert report["excluded"] == {
        "6": "the second page: pagination is not part of this SDK",
        "7": "static resource",
    }


def test_a_header_that_differs_from_the_capture_fails_g7(project: Path, work: Path) -> None:
    report = _replay(work, write_project(project, origin="https://other.example"))
    assert report["gates"]["G7 requests match the recorded ones"] is False
    assert report["gates"]["G4 every scenario ends as expected"] is True
    assert (
        "#2 header Origin: sent 'https://other.example', recorded 'https://id.site.example'"
        in report["scenarios"][0]["findings"]
    )


def test_a_query_value_that_differs_is_named(project: Path, work: Path) -> None:
    report = _replay(work, write_project(project, limit=50))
    findings = report["scenarios"][1]["findings"]
    assert findings == ["#5 query 'limit': the SDK sends '50', recorded '2'"]


def test_a_timestamp_is_allowed_to_differ(project: Path, work: Path) -> None:
    report = _replay(work, write_project(project))
    sent = report["scenarios"][1]["requests"][-1]
    assert sent["exchange"] == 5
    assert sent["findings"] == []


def test_a_request_outside_the_capture_fails_g5(project: Path, work: Path) -> None:
    scenarios = write_project(project)
    scenarios.write_text(
        scenarios.read_text(encoding="utf-8").replace(
            "exchanges = [1, 3, 4, 5]", "exchanges = [1, 3]"
        ),
        encoding="utf-8",
    )
    report = _replay(work, scenarios)
    assert report["gates"]["G5 no request outside the capture"] is False
    assert report["scenarios"][1]["not_in_capture"] == ["GET mail.site.example/inbox/ ?"]
    assert report["scenarios"][1]["as_expected"] is False


def test_an_exchange_nobody_claims_fails_g6(project: Path, work: Path) -> None:
    scenarios = write_project(project)
    text = scenarios.read_text(encoding="utf-8").split("[[exclude]]")[0]
    scenarios.write_text(text, encoding="utf-8")
    report = _replay(work, scenarios)
    assert report["uncovered_exchanges"] == [6]
    assert report["gates"]["G6 every exchange is used or excluded"] is False


def test_an_exchange_the_sdk_never_asks_for_fails_g6(project: Path, work: Path) -> None:
    scenarios = write_project(project)
    scenarios.write_text(
        scenarios.read_text(encoding="utf-8").replace('exchanges = "1-2"', 'exchanges = "1-2,4"'),
        encoding="utf-8",
    )
    report = _replay(work, scenarios)
    assert report["scenarios"][0]["unused_exchanges"] == [4]
    assert report["gates"]["G6 every exchange is used or excluded"] is False


def test_a_wrong_expectation_fails_g4(project: Path, work: Path) -> None:
    scenarios = write_project(project)
    scenarios.write_text(
        scenarios.read_text(encoding="utf-8").replace("threads_total = 5", "threads_total = 6"),
        encoding="utf-8",
    )
    report = _replay(work, scenarios)
    assert report["scenarios"][1]["problems"] == ["threads_total: expected 6, got 5"]
    assert report["gates"]["G4 every scenario ends as expected"] is False


def test_secrets_never_reach_the_report(project: Path, work: Path) -> None:
    report = _replay(work, write_project(project, origin="https://other.example"))
    text = json.dumps(report, ensure_ascii=False) + sdk_replay.render(report)
    for value in sdk_replay.load_secrets(work).values():
        assert value not in text
    assert PASSWORD not in text


def test_scenarios_file_is_validated(tmp_path: Path) -> None:
    path = tmp_path / "scenarios.toml"
    path.write_text('[sdk]\nfactory = "nope"\n', encoding="utf-8")
    with pytest.raises(sdk_replay.ScenarioError, match="module:callable"):
        sdk_replay.load_plan(path)
    path.write_text(
        '[sdk]\nfactory = "a:b"\n[[scenario]]\nname = "x"\nexchanges = "1"\ncall = "c"\n',
        encoding="utf-8",
    )
    with pytest.raises(sdk_replay.ScenarioError, match="exactly one of raises/returns"):
        sdk_replay.load_plan(path)
    path.write_text(
        '[sdk]\nfactory = "a:b"\n[[scenario]]\nname = "x"\nexchanges = "1"\ncall = "c"\n'
        '[scenario.expect]\nreturns = "T"\n[[exclude]]\nexchanges = "2"\n',
        encoding="utf-8",
    )
    with pytest.raises(sdk_replay.ScenarioError, match="needs a reason"):
        sdk_replay.load_plan(path)


def test_ranges_are_read() -> None:
    assert sdk_replay.parse_range("1-3, 7") == [1, 2, 3, 7]
    assert sdk_replay.parse_range([4, 5]) == [4, 5]


# --- the whole check ----------------------------------------------------------------------


def test_check_passes_on_a_clean_project(project: Path, work: Path) -> None:
    report = sdk_check.run(work, "minisdk", write_project(project), project, tools=False)
    statuses = {name.split()[0]: item["status"] for name, item in report["gates"].items()}
    assert statuses == {
        "G1": "pass",
        "G2": "skipped",
        "G3": "skipped",
        "G4": "pass",
        "G5": "pass",
        "G6": "pass",
        "G7": "pass",
        "G8": "pass",
        "G9": "pass",
        "G10": statuses["G10"],
    }
    # A skipped gate is not a pass: the report says so.
    assert report["passed"] is False


def test_a_secret_copied_into_the_project_fails_g8(project: Path, work: Path) -> None:
    scenarios = write_project(project)
    (project / "minisdk" / "notes.py").write_text(f'DEFAULT = "{PASSWORD}"\n', encoding="utf-8")
    report = sdk_check.run(work, "minisdk", scenarios, project, tools=False)
    result = report["gates"]["G8 no secrets in the project"]
    assert result["status"] == "fail"
    assert result["details"] == ["minisdk/notes.py:1: __SECRET_password_2__"]


def test_the_work_directory_itself_is_not_scanned(project: Path, work: Path) -> None:
    scenarios = write_project(project)
    inside = project / "work"
    inside.mkdir()
    for path in work.iterdir():
        (inside / path.name).write_bytes(path.read_bytes())
    report = sdk_check.run(inside, "minisdk", scenarios, project, tools=False)
    assert report["gates"]["G8 no secrets in the project"]["status"] == "pass"


def test_a_private_import_fails_g9(project: Path, work: Path) -> None:
    scenarios = write_project(project)
    (project / "minisdk" / "hack.py").write_text(
        "from eazy_sdk.request._mapping import validate_responses\n"
        "from eazy_sdk.response import _CaseReading\n"
        "from eazy_sdk.response import Location\n",
        encoding="utf-8",
    )
    result = sdk_check.check_private_imports(project, [work])
    assert result["status"] == "fail"
    assert result["details"] == [
        "minisdk/hack.py:1: eazy_sdk.request._mapping",
        "minisdk/hack.py:2: eazy_sdk.response._CaseReading",
    ]
    assert scenarios.exists()


def test_a_module_that_does_not_import_fails_g1(project: Path, work: Path) -> None:
    write_project(project)
    (project / "minisdk" / "broken.py").write_text(
        "import no_such_module_anywhere\n", encoding="utf-8"
    )
    result = sdk_check.check_imports("minisdk")
    assert result["status"] == "fail"
    assert result["details"][0].startswith("minisdk.broken: ModuleNotFoundError")
    assert work.exists()


def test_a_cookie_from_before_the_scenario_is_a_note_not_a_difference() -> None:
    from capture_model import Exchange

    recorded = Exchange(
        1,
        "GET",
        "https://a.example/x",
        request_headers=[("Cookie", "old=1; fresh=2")],
    )
    sent = sdk_replay.Sent("GET", "https://a.example/x", (), b"")
    shield = sdk_replay.Shield({})
    findings, notes = sdk_replay.compare(sent, recorded, shield, frozenset({"fresh"}))
    assert findings == ["cookies: recorded ['fresh'], not sent by the SDK"]
    assert notes == ["cookies ['old'] were set before this scenario began"]
    findings, _ = sdk_replay.compare(sent, recorded, shield)
    assert findings == ["cookies: recorded ['fresh', 'old'], not sent by the SDK"]
