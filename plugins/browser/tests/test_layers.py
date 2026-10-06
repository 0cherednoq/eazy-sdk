"""Граница слоёв — проверка, а не договорённость (`docs/LAYERS.md` §0, B7.6).

Плагин знает сайт, а не среду: он не запускает браузер, не создаёт контексты и вкладки,
не держит очередей и семафоров. Здесь это проверяется разбором кода всех модулей пакета —
ядра, адаптеров, интеграций и фейков, — чтобы граница не зависела от внимательности
ревьюера. Исключения — списком, а не по умолчанию: всё, чего в списке нет, — находка.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

import eazy_sdk_browser
import pytest

pytestmark = pytest.mark.unit

PACKAGE = Path(eazy_sdk_browser.__file__).parent

ORCHESTRATION_CALLS = frozenset(
    {
        "launch",
        "launch_persistent_context",
        "new_context",
        "new_page",
        "add_init_script",
        "clear_cookies",
        "set_extra_http_headers",
    }
)
"""Методы, которые создают браузер, контекст или вкладку либо меняют весь контекст."""

ORCHESTRATION_PRIMITIVES = frozenset({"Semaphore", "Queue", "Condition"})
"""Примитивы `asyncio`, из которых строят очереди и лимиты — забота слоя выше."""

ALLOWED: dict[str, frozenset[str]] = {
    "asyncio.Lock": frozenset({"session.py"}),
    "SessionLifecycle": frozenset({"session.py"}),
    "context.add_cookies": frozenset({"handlers/playwright.py"}),
    "add_cookies()": frozenset({"session.py", "handlers/playwright.py"}),
}
"""Разрешённые исключения: жизненный цикл сессии одного контекста — только `session.py`;
запись кук в контекст — только реализация `StateAware.add_cookies` у драйвера, а зовёт её
только `session.py`."""


@dataclass(frozen=True, slots=True)
class Finding:
    module: str
    line: int
    what: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line}: {self.what}"


def modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def relative(path: Path) -> str:
    return path.relative_to(PACKAGE).as_posix()


def findings_in(path: Path) -> list[Finding]:
    module = relative(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        Finding(module, getattr(node, "lineno", 0), what)
        for node in ast.walk(tree)
        for what in _suspects(node)
        if module not in ALLOWED.get(what, frozenset())
    ]


def _suspects(node: ast.AST) -> list[str]:
    """Что в узле похоже на оркестрацию или на запись в контекст."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _call_suspects(node.func)
    if isinstance(node, ast.Attribute) and _is_name(node.value, "asyncio"):
        return _asyncio_suspects(node.attr)
    if isinstance(node, ast.ImportFrom):
        return _import_suspects(node)
    if isinstance(node, ast.Name) and node.id == "SessionLifecycle":
        return ["SessionLifecycle"]
    return []


def _call_suspects(func: ast.Attribute) -> list[str]:
    if func.attr in ORCHESTRATION_CALLS:
        return [f"{func.attr}()"]
    if func.attr != "add_cookies":
        return []
    receiver = func.value
    if isinstance(receiver, ast.Attribute) and receiver.attr == "context":
        return ["context.add_cookies"]
    return ["add_cookies()"]


def _asyncio_suspects(attr: str) -> list[str]:
    if attr == "Lock":
        return ["asyncio.Lock"]
    if attr in ORCHESTRATION_PRIMITIVES:
        return [f"asyncio.{attr}"]
    return []


def _import_suspects(node: ast.ImportFrom) -> list[str]:
    names = {alias.name for alias in node.names}
    if node.module == "asyncio":
        return [f"asyncio.{name}" for name in sorted(names & {"Lock", *ORCHESTRATION_PRIMITIVES})]
    if "SessionLifecycle" in names:
        return ["SessionLifecycle"]
    return []


def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def test_the_plugin_does_not_orchestrate_browsers_contexts_or_tabs() -> None:
    found = [finding for path in modules() for finding in findings_in(path)]

    assert not found, "оркестрация в плагине:\n" + "\n".join(map(str, found))


def test_every_package_module_is_checked() -> None:
    """Проверка видит и ядро, и адаптеры, и интеграции, и фейки — не только верхний уровень."""
    checked = {relative(path) for path in modules()}

    assert {
        "client.py",
        "login.py",
        "session.py",
        "testing.py",
        "handlers/playwright.py",
        "integrations/accounts.py",
    } <= checked


def test_the_checker_finds_what_it_forbids(tmp_path: Path) -> None:
    """Сам проверяющий не слеп: всё запрещённое в чужом модуле — находка, по порядку строк."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "import asyncio\n"
        "from asyncio import Semaphore\n"
        "async def open(browser, page):\n"
        "    context = await browser.new_context()\n"
        "    await page.context.add_cookies([])\n"
        "    return asyncio.Lock(), asyncio.Queue()\n",
        encoding="utf-8",
    )

    found = [finding.what for finding in _findings_outside_package(offender)]

    assert found == [
        "asyncio.Semaphore",
        "new_context()",
        "context.add_cookies",
        "asyncio.Lock",
        "asyncio.Queue",
    ]


def _findings_outside_package(path: Path) -> list[Finding]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.stmt | ast.expr)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    return [Finding(path.name, node.lineno, what) for node in nodes for what in _suspects(node)]
