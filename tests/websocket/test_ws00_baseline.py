from __future__ import annotations

import ast
import socket
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from zapros.websocket import BinaryMessage, CloseMessage, TextMessage

from tests.websocket._support import (
    DeterministicClock,
    FakeConnector,
    FakeWebSocket,
    ScriptedBackoff,
    ScriptExhausted,
    assert_no_task_leaks,
)

ROOT = Path(__file__).parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return imported


def test_websocket_extra_is_pinned_and_included_in_all_and_dev() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = config["project"]["optional-dependencies"]
    dev = config["dependency-groups"]["dev"]

    assert extras["websocket"] == ["zapros[websocket]==0.16.0"]
    assert "zapros[websocket]==0.16.0" in extras["all"]
    assert "zapros[pyreqwest,websocket]==0.16.0" in dev


async def test_fake_boundary_scripts_frames_connections_time_and_cleanup() -> None:
    websocket = FakeWebSocket(
        [TextMessage("ready"), BinaryMessage(b"payload"), CloseMessage(1000, "done")]
    )
    connector = FakeConnector([websocket])
    clock = DeterministicClock(10.0)
    backoff = ScriptedBackoff([0.25, 0.5], clock=clock)

    with patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
        async with assert_no_task_leaks():
            async with connector(
                "wss://example.test/events",
                client=object(),
                subprotocols=["graphql-transport-ws"],
                permessage_deflate=True,
            ) as connection:
                received = [message async for message in connection]
                await backoff.wait(1)
                await backoff.wait(2)

    assert received == [
        TextMessage("ready"),
        BinaryMessage(b"payload"),
        CloseMessage(1000, "done"),
    ]
    assert connector.attempts[0].url == "wss://example.test/events"
    assert connector.attempts[0].subprotocols == ("graphql-transport-ws",)
    assert connector.attempts[0].permessage_deflate is True
    assert connection.close_code == 1000
    assert connection.close_reason == "done"
    assert backoff.waits == [(1, 0.25), (2, 0.5)]
    assert clock.now() == 10.75


async def test_fake_boundary_surfaces_scripted_failures_and_exhaustion() -> None:
    failure = RuntimeError("handshake failed")
    connector = FakeConnector([failure, FakeWebSocket()])

    with pytest.raises(RuntimeError, match="handshake failed"):
        async with connector("wss://example.test/first"):
            pass

    async with connector("wss://example.test/second") as websocket:
        with pytest.raises(ScriptExhausted, match="receive script"):
            await websocket.recv()

    with pytest.raises(ScriptExhausted, match="connector script"):
        async with connector("wss://example.test/third"):
            pass


def test_http_runtime_does_not_import_websocket_boundary() -> None:
    forbidden = {"zapros.websocket", "wsproto", "eazy_sdk.websocket"}
    websocket_root = ROOT / "eazy_sdk" / "websocket"

    for path in (ROOT / "eazy_sdk").rglob("*.py"):
        if websocket_root in path.parents:
            continue
        imported = _imports(path)
        assert not any(
            name == blocked or name.startswith(f"{blocked}.")
            for name in imported
            for blocked in forbidden
        ), path


def test_core_import_does_not_load_optional_websocket_stack() -> None:
    script = """
import sys
import eazy_sdk

loaded = set(sys.modules)
assert "zapros.websocket" not in loaded
assert not any(name == "wsproto" or name.startswith("wsproto.") for name in loaded)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_runtime_has_no_universal_http_websocket_request_or_executor() -> None:
    forbidden = {
        "UnifiedExecutor",
        "UnifiedRequest",
        "UniversalExecutor",
        "UniversalRequest",
    }

    for path in (ROOT / "eazy_sdk").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert declared.isdisjoint(forbidden), path
