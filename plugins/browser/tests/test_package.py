import subprocess
import sys
import tomllib
from pathlib import Path

import eazy_sdk_browser
import pytest

# Тест поднимает отдельный интерпретатор: это дольше корневого `timeout = 10`.
pytestmark = pytest.mark.timeout(60)

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.unit
def test_version_matches_project_metadata() -> None:
    """Версия пакета и версия в pyproject.toml не расходятся."""
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert eazy_sdk_browser.__version__ == declared["project"]["version"]


@pytest.mark.unit
def test_core_imports_no_driver() -> None:
    """Ядро не тянет драйвер при импорте: transport-agnostic — это не лозунг.

    Проверка идёт в отдельном процессе: в самой сессии playwright уже импортирован
    тестами адаптера, и здесь он нашёлся бы всегда.
    """
    probe = (
        "import sys, eazy_sdk_browser;"
        "drivers = {'playwright', 'selenium', 'camoufox', 'pydoll'};"
        "loaded = drivers & {name.split('.')[0] for name in sys.modules};"
        "print(sorted(loaded))"
    )

    result = subprocess.run(  # noqa: S603 — свой же интерпретатор с литеральной программой
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )

    assert result.stdout.strip() == "[]"
