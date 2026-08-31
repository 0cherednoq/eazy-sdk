"""Tests for scripts/docs_freshness.py (docs staleness detection)."""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import docs_freshness  # noqa: E402

FAKE_MODULE = "_docs_freshness_fake_mod"


def _install_fake_module(*, with_extra_param: bool) -> types.ModuleType:
    module = types.ModuleType(FAKE_MODULE)
    namespace: dict[str, object] = {}
    extra = ", retries: int = 0" if with_extra_param else ""
    exec(  # builds a controlled fake API surface whose signature the test can vary
        f"class Client:\n    def fetch(self, url: str{extra}) -> str:\n        return url\n",
        namespace,
    )
    module.Client = namespace["Client"]  # type: ignore[attr-defined]
    sys.modules[FAKE_MODULE] = module
    return module


@pytest.fixture
def fake_module() -> Iterator[types.ModuleType]:
    module = _install_fake_module(with_extra_param=False)
    yield module
    sys.modules.pop(FAKE_MODULE, None)


@pytest.fixture
def docs_tree(tmp_path: Path) -> tuple[Path, Path]:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    lock_file = tmp_path / "fingerprints.lock.json"
    return docs_dir, lock_file


def _write_page(docs_dir: Path, rel: str, *, sources: list[str], extra_lines: str = "") -> Path:
    page = docs_dir / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {dotted}" for dotted in sources)
    page.write_text(
        f"---\ntitle: Test page\nsources:\n{sources_yaml}\n"
        f'api_fingerprint: "sha256:pending"\n{extra_lines}---\n\nBody text.\n',
        encoding="utf-8",
    )
    return page


def _run(args: list[str], docs_dir: Path, lock_file: Path) -> int:
    command, *rest = args
    return docs_freshness.main(
        [command, "--docs-dir", str(docs_dir), "--lock-file", str(lock_file), *rest]
    )


def _check_json(
    capsys: pytest.CaptureFixture[str], docs_dir: Path, lock_file: Path
) -> tuple[int, dict[str, object]]:
    code = _run(["check", "--json"], docs_dir, lock_file)
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, dict)
    return code, payload


class TestFreshScenario:
    def test_update_then_check_passes(
        self,
        fake_module: types.ModuleType,
        docs_tree: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        docs_dir, lock_file = docs_tree
        _write_page(docs_dir, "guide.md", sources=[f"{FAKE_MODULE}.Client"])

        assert _run(["update"], docs_dir, lock_file) == 0
        capsys.readouterr()
        code, payload = _check_json(capsys, docs_dir, lock_file)

        assert code == 0
        assert payload["stale"] == []
        assert payload["checked"] == 1

    def test_draft_and_freshness_false_pages_are_skipped(
        self,
        docs_tree: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        docs_dir, lock_file = docs_tree
        (docs_dir / "draft.md").write_text(
            "---\ntitle: Draft\ndraft: true\n---\n", encoding="utf-8"
        )
        (docs_dir / "landing.md").write_text(
            "---\ntitle: Landing\nfreshness: false\n---\n", encoding="utf-8"
        )

        code, payload = _check_json(capsys, docs_dir, lock_file)

        assert code == 0
        assert payload["checked"] == 0


class TestStaleScenario:
    def test_signature_change_marks_page_stale_with_symbol(
        self,
        fake_module: types.ModuleType,
        docs_tree: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        docs_dir, lock_file = docs_tree
        _write_page(docs_dir, "guide.md", sources=[f"{FAKE_MODULE}.Client"])
        assert _run(["update"], docs_dir, lock_file) == 0
        capsys.readouterr()

        _install_fake_module(with_extra_param=True)
        code, payload = _check_json(capsys, docs_dir, lock_file)

        assert code == 1
        stale = payload["stale"]
        assert stale == [
            {
                "page": "guide.md",
                "stale_symbols": [f"{FAKE_MODULE}.Client"],
                "missing_symbols": [],
            }
        ]

    def test_pending_fingerprint_is_stale(
        self,
        fake_module: types.ModuleType,
        docs_tree: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        docs_dir, lock_file = docs_tree
        _write_page(docs_dir, "guide.md", sources=[f"{FAKE_MODULE}.Client"])

        code, payload = _check_json(capsys, docs_dir, lock_file)

        assert code == 1
        assert isinstance(payload["stale"], list)
        assert len(payload["stale"]) == 1

    def test_missing_symbol_reported(
        self,
        fake_module: types.ModuleType,
        docs_tree: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        docs_dir, lock_file = docs_tree
        _write_page(
            docs_dir,
            "guide.md",
            sources=[f"{FAKE_MODULE}.Client", f"{FAKE_MODULE}.Gone"],
        )

        code, payload = _check_json(capsys, docs_dir, lock_file)

        assert code == 1
        stale = payload["stale"]
        assert isinstance(stale, list)
        entry = stale[0]
        assert isinstance(entry, dict)
        assert entry["missing_symbols"] == [f"{FAKE_MODULE}.Gone"]


class TestUpdateCommand:
    def test_update_rewrites_only_fingerprint_line(
        self,
        fake_module: types.ModuleType,
        docs_tree: tuple[Path, Path],
    ) -> None:
        docs_dir, lock_file = docs_tree
        page = _write_page(
            docs_dir,
            "guide.md",
            sources=[f"{FAKE_MODULE}.Client"],
            extra_lines="sidebar:\n  order: 3\n",
        )
        before = page.read_text(encoding="utf-8")

        assert _run(["update", "guide.md"], docs_dir, lock_file) == 0
        after = page.read_text(encoding="utf-8")

        new_fingerprint = docs_freshness.page_fingerprint(
            {f"{FAKE_MODULE}.Client": docs_freshness.symbol_fingerprint(f"{FAKE_MODULE}.Client")}
        )
        expected = before.replace(
            'api_fingerprint: "sha256:pending"', f'api_fingerprint: "{new_fingerprint}"'
        )
        assert after == expected

    def test_update_writes_lock_file_with_symbol_hashes(
        self,
        fake_module: types.ModuleType,
        docs_tree: tuple[Path, Path],
    ) -> None:
        docs_dir, lock_file = docs_tree
        _write_page(docs_dir, "guide.md", sources=[f"{FAKE_MODULE}.Client"])

        assert _run(["update"], docs_dir, lock_file) == 0

        lock = docs_freshness.load_lock(lock_file)
        assert set(lock) == {"guide.md"}
        assert set(lock["guide.md"].symbols) == {f"{FAKE_MODULE}.Client"}

    def test_update_fails_on_missing_symbol_without_writing(
        self,
        fake_module: types.ModuleType,
        docs_tree: tuple[Path, Path],
    ) -> None:
        docs_dir, lock_file = docs_tree
        page = _write_page(docs_dir, "guide.md", sources=[f"{FAKE_MODULE}.Gone"])
        before = page.read_text(encoding="utf-8")

        assert _run(["update"], docs_dir, lock_file) == 1
        assert page.read_text(encoding="utf-8") == before

    def test_update_unknown_page_errors(
        self,
        fake_module: types.ModuleType,
        docs_tree: tuple[Path, Path],
    ) -> None:
        docs_dir, lock_file = docs_tree
        _write_page(docs_dir, "guide.md", sources=[f"{FAKE_MODULE}.Client"])

        assert _run(["update", "nope.md"], docs_dir, lock_file) == 1
