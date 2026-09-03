"""Phase 34: layer contracts enforced from import graphs (core -> compile -> clients)."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "eazy_sdk"


def _modules(prefix: str) -> Iterator[tuple[str, Path]]:
    base = PACKAGE / prefix.replace(".", "/")
    files = [base] if base.suffix == ".py" else list(base.rglob("*.py"))
    if not files and base.with_suffix(".py").exists():
        files = [base.with_suffix(".py")]
    for path in files:
        if path.is_file():
            yield path.relative_to(ROOT).as_posix(), path


def _imports(path: Path, *, top_level_only: bool) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    nodes = tree.body if top_level_only else list(ast.walk(tree))
    for node in nodes:
        if top_level_only and isinstance(node, ast.If):
            # ``if TYPE_CHECKING:`` blocks are typing-only, not runtime edges.
            continue
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _violations(
    prefix: str, forbidden: tuple[str, ...], *, top_level_only: bool = True
) -> list[str]:
    found: list[str] = []
    for rel, path in _modules(prefix):
        for name in _imports(path, top_level_only=top_level_only):
            if any(name == item or name.startswith(item + ".") for item in forbidden):
                found.append(f"{rel} -> {name}")
    return sorted(found)


def test_core_imports_nothing_from_the_package() -> None:
    # Lazy model-adapter imports stay inside functions; module-level edges are forbidden.
    assert _violations("core", ("eazy_sdk",)) == []
    assert (PACKAGE / "core" / "__init__.py").exists()
    assert not (PACKAGE / "_internal").exists()


def test_compile_sits_above_request_response_and_below_clients() -> None:
    upper = ("eazy_sdk.clients", "eazy_sdk.api", "eazy_sdk.preparation")
    assert _violations("compile", upper) == []
    for layer in ("request", "response", "codecs", "models"):
        forbidden = ("eazy_sdk.compile", "eazy_sdk.clients", "eazy_sdk._internal")
        assert _violations(layer, forbidden) == [], layer


def test_ext_is_built_on_core_and_public_layers_only() -> None:
    forbidden = (
        "eazy_sdk._internal",
        "eazy_sdk.compile",
        "eazy_sdk.clients",
        "eazy_sdk_accounts",
        "eazy_sdk_accounts.storage",
    )
    assert _violations("ext", forbidden) == []


def test_policies_are_neutral_and_preparation_does_not_import_clients() -> None:
    assert _violations("policies.py", ("eazy_sdk.clients", "eazy_sdk.compile")) == []
    assert _violations("preparation.py", ("eazy_sdk.clients",)) == []
    from eazy_sdk.clients import CallOptions as ClientsCallOptions
    from eazy_sdk.policies import CallOptions, RetryPolicy

    assert ClientsCallOptions is CallOptions
    assert RetryPolicy.none().retries == 0


def test_clients_do_not_import_accounts_and_extraction_keeps_pydantic_lazy() -> None:
    assert _violations("clients", ("eazy_sdk_accounts", "eazy_sdk_accounts.storage")) == []
    assert _violations("extraction", ("eazy_sdk.pydantic_integration",)) == []
    from eazy_sdk.auth.lifecycle import LifecycleGraph
    from eazy_sdk.auth.session import LifecycleGraph as AccountsGraph

    assert AccountsGraph is LifecycleGraph


def test_compiler_depends_on_the_crypto_port_not_the_crypto_package() -> None:
    assert _violations("compile/http_compiler.py", ("eazy_sdk.crypto",)) == []
    assert _violations("compile/http_operation.py", ("eazy_sdk.crypto",)) == []
    from eazy_sdk.core.ports import CryptoProfile
    from eazy_sdk.crypto import PayloadCrypto

    port_members = {"name", "outbound", "inbound", "inputs"}
    assert port_members <= set(PayloadCrypto.__dataclass_fields__)
    assert port_members <= set(CryptoProfile.__protocol_attrs__)  # type: ignore[attr-defined]
