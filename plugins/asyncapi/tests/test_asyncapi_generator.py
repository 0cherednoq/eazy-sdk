from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from eazy_sdk_asyncapi import AsyncApiDiagnosticError, generate_package, parse_asyncapi
from eazy_sdk_asyncapi.cli import load_document, main
from zapros.websocket import TextMessage

from eazy_sdk.websocket import AsyncWsClient
from tests.websocket._support import FakeConnector, LiveFakeWebSocket, assert_no_task_leaks


def _document() -> dict[str, object]:
    return {
        "asyncapi": "3.0.0",
        "info": {"title": "Market stream", "version": "1.0.0"},
        "servers": {
            "production": {
                "host": "stream.example.test",
                "pathname": "/ws",
                "protocol": "wss",
            }
        },
        "channels": {
            "market": {
                "address": "markets/{marketId}",
                "parameters": {
                    "marketId": {"description": "Market identifier"},
                },
                "messages": {
                    "Publish": {"$ref": "#/components/messages/Publish"},
                    "LookupResult": {"$ref": "#/components/messages/LookupResult"},
                    "Tick": {"$ref": "#/components/messages/Tick"},
                },
            }
        },
        "operations": {
            "publish": {
                "action": "send",
                "channel": {"$ref": "#/channels/market"},
                "messages": [{"$ref": "#/channels/market/messages/Publish"}],
            },
            "lookup": {
                "action": "send",
                "channel": {"$ref": "#/channels/market"},
                "messages": [{"$ref": "#/channels/market/messages/Publish"}],
                "reply": {"messages": [{"$ref": "#/channels/market/messages/LookupResult"}]},
            },
            "observe": {
                "action": "receive",
                "channel": {"$ref": "#/channels/market"},
                "messages": [{"$ref": "#/channels/market/messages/Tick"}],
            },
            "prices": {
                "action": "receive",
                "channel": {"$ref": "#/channels/market"},
                "messages": [{"$ref": "#/channels/market/messages/Tick"}],
                "x-eazy-sdk.websocket": {
                    "kind": "subscribe",
                    "discriminator": "subscribe",
                },
            },
        },
        "components": {
            "messages": {
                "Publish": {
                    "name": "publish",
                    "correlationId": {"location": "$message.header#/correlationId"},
                    "payload": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                },
                "LookupResult": {
                    "name": "lookup_result",
                    "payload": {
                        "type": "object",
                        "properties": {"accepted": {"type": "boolean"}},
                        "required": ["accepted"],
                    },
                },
                "Tick": {
                    "name": "tick",
                    "payload": {
                        "type": "object",
                        "properties": {"price": {"type": "integer"}},
                        "required": ["price"],
                    },
                },
            }
        },
    }


def test_asyncapi_ir_preserves_refs_identity_and_lowering() -> None:
    ir = parse_asyncapi(_document())

    assert ir.servers[0].url == "wss://stream.example.test/ws"
    assert ir.channels[0].address == "markets/{marketId}"
    assert ir.channels[0].parameters == ("marketId",)
    publish = next(operation for operation in ir.operations if operation.operation_id == "publish")
    lookup = next(operation for operation in ir.operations if operation.operation_id == "lookup")
    observe = next(operation for operation in ir.operations if operation.operation_id == "observe")
    prices = next(operation for operation in ir.operations if operation.operation_id == "prices")
    assert publish.kind == "send"
    assert lookup.kind == "call"
    assert observe.kind == "event"
    assert prices.kind == "subscribe"
    assert publish.messages[0] is ir.messages[0]
    assert lookup.messages[0] is publish.messages[0]
    assert ir.messages[0].correlation_location == "$message.header#/correlationId"


def test_crypto_extension_lowers_named_profile_and_frame_policy(tmp_path: Path) -> None:
    document = _document()
    operations = document["operations"]
    assert isinstance(operations, dict)
    lookup = operations["lookup"]
    assert isinstance(lookup, dict)
    lookup["x-eazy-sdk-crypto"] = {
        "profile": "payments-v1",
        "direction": "bidirectional",
        "wire": {
            "frameKind": "text",
            "textSafe": True,
            "clearFrameKind": "text",
        },
    }

    ir = parse_asyncapi(document)
    lookup_ir = next(item for item in ir.operations if item.operation_id == "lookup")
    assert lookup_ir.crypto is not None
    assert lookup_ir.crypto["profile"] == "payments-v1"
    package = generate_package(
        document,
        spec_path=tmp_path / "market.json",
        output_directory=tmp_path / "generated",
        package_name="market_crypto",
    )
    source = (package / "protocol.py").read_text(encoding="utf-8")
    regenerated = generate_package(
        document,
        spec_path=tmp_path / "market.json",
        output_directory=tmp_path / "generated",
        package_name="market_crypto",
    )
    assert (regenerated / "protocol.py").read_text(encoding="utf-8") == source
    compile(source, str(package / "protocol.py"), "exec")
    assert "def crypto_registry(profiles: Mapping[str, PayloadCrypto])" in source
    assert "operation_ids=('lookup',)" in source
    assert "frame_kind='text'" in source
    assert "text_safe=True" in source
    assert all(token not in source for token in ("lambda", "eval(", "import_module", "key="))
    typing = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package)],
        capture_output=True,
        text=True,
    )
    assert typing.returncode == 0, typing.stdout + typing.stderr
    sys.path.insert(0, str(tmp_path / "generated"))
    try:
        module = importlib.import_module("market_crypto")
        with pytest.raises(KeyError, match="payments-v1"):
            module.crypto_registry({})
    finally:
        sys.path.remove(str(tmp_path / "generated"))
        for name in tuple(sys.modules):
            if name == "market_crypto" or name.startswith("market_crypto."):
                del sys.modules[name]


def test_crypto_extension_rejects_unsafe_text_frame() -> None:
    document = _document()
    operations = document["operations"]
    assert isinstance(operations, dict)
    lookup = operations["lookup"]
    assert isinstance(lookup, dict)
    lookup["x-eazy-sdk-crypto"] = {
        "profile": "payments-v1",
        "wire": {"frameKind": "text"},
    }

    with pytest.raises(AsyncApiDiagnosticError) as captured:
        parse_asyncapi(document)

    assert captured.value.pointer.endswith("/wire/textSafe")


def test_json_and_yaml_load_to_same_deterministic_generation(tmp_path: Path) -> None:
    yaml = pytest.importorskip("yaml")
    json_path = tmp_path / "market.json"
    yaml_path = tmp_path / "market.yaml"
    json_path.write_text(json.dumps(_document()), encoding="utf-8")
    yaml_path.write_text(yaml.safe_dump(_document(), sort_keys=False), encoding="utf-8")

    json_output = tmp_path / "json-output"
    yaml_output = tmp_path / "yaml-output"
    generate_package(
        load_document(json_path),
        spec_path=json_path,
        output_directory=json_output,
        package_name="market_stream",
    )
    assert main([str(yaml_path), str(yaml_output), "--package-name", "market_stream"]) == 0

    json_files = {
        path.relative_to(json_output): path.read_text(encoding="utf-8")
        for path in json_output.rglob("*.py")
    }
    yaml_files = {
        path.relative_to(yaml_output): path.read_text(encoding="utf-8")
        for path in yaml_output.rglob("*.py")
    }
    assert json_files == yaml_files
    assert set(json_files) == {
        Path("market_stream/__init__.py"),
        Path("market_stream/client.py"),
        Path("market_stream/models.py"),
        Path("market_stream/protocol.py"),
    }


async def test_generated_package_imports_types_and_executes_on_common_runtime(
    tmp_path: Path,
) -> None:
    output = tmp_path / "generated"
    generate_package(
        _document(),
        spec_path=tmp_path / "market.json",
        output_directory=output,
        package_name="market_stream_runtime",
    )
    sys.path.insert(0, str(output))
    try:
        package = importlib.import_module("market_stream_runtime")
        models = importlib.import_module("market_stream_runtime.models")
        connection = LiveFakeWebSocket()
        async with (
            assert_no_task_leaks(),
            AsyncWsClient(
                endpoint="wss://example.test/ws",
                protocol=package.PROTOCOL,
                connector=FakeConnector([connection]),
            ) as client,
        ):
            api = package.AsyncAPI(client)
            await api.publish("hello")
            lookup = asyncio.create_task(api.lookup("question"))
            for _ in range(200):
                if len(connection.sent) == 2:
                    break
                await asyncio.sleep(0)
            connection.feed(
                TextMessage('{"type":"lookup_result","id":"1","data":{"accepted":true}}')
            )
            assert await lookup == models.LookupResult(accepted=True)

            subscription = await api.prices()
            connection.feed(TextMessage('{"type":"tick","id":"2","data":{"price":42}}'))
            assert (await anext(subscription)).value == models.Tick(price=42)
    finally:
        sys.path.remove(str(output))
        for name in tuple(sys.modules):
            if name == "market_stream_runtime" or name.startswith("market_stream_runtime."):
                sys.modules.pop(name)


def test_generated_source_is_thin_and_passes_strict_mypy(tmp_path: Path) -> None:
    output = tmp_path / "generated"
    package = generate_package(
        _document(),
        spec_path=tmp_path / "market.json",
        output_directory=output,
        package_name="market_stream_typing",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py"))
    assert "reader_loop" not in source
    assert "reconnect" not in source
    assert "add_done_callback" not in source
    assert "@ws.send" in source
    assert "@ws.call" in source
    assert "@ws.subscribe" in source

    environment = dict(__import__("os").environ)
    environment["MYPYPATH"] = str(output)
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package)],
        cwd=Path(__file__).parents[3],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_unsupported_binding_has_operation_pointer_and_reference_chain() -> None:
    document = _document()
    operations = document["operations"]
    assert isinstance(operations, dict)
    publish = operations["publish"]
    assert isinstance(publish, dict)
    publish["bindings"] = {"kafka": {}}

    with pytest.raises(AsyncApiDiagnosticError) as captured:
        parse_asyncapi(document)

    assert captured.value.operation_id == "publish"
    assert captured.value.pointer == "#/operations/publish/bindings"
    assert captured.value.reference_chain == ("#/operations/publish",)


def test_unsupported_schema_format_reports_referencing_operation() -> None:
    document = _document()
    components = document["components"]
    assert isinstance(components, dict)
    messages = components["messages"]
    assert isinstance(messages, dict)
    publish = messages["Publish"]
    assert isinstance(publish, dict)
    payload = publish["payload"]
    assert isinstance(payload, dict)
    properties = payload["properties"]
    assert isinstance(properties, dict)
    value = properties["value"]
    assert isinstance(value, dict)
    value["format"] = "unsupported-wire-format"

    with pytest.raises(AsyncApiDiagnosticError) as captured:
        parse_asyncapi(document)

    assert captured.value.operation_id == "publish"
    assert captured.value.pointer.endswith("/payload/properties/value/format")
    assert captured.value.reference_chain[0] == "#/operations/publish"
    assert captured.value.reference_chain[-1] == "#/components/messages/Publish"


def test_canonical_websocket_extension_lowers_policies_errors_signing_and_protocol(
    tmp_path: Path,
) -> None:
    document = _document()
    components = document["components"]
    operations = document["operations"]
    assert isinstance(components, dict)
    assert isinstance(operations, dict)
    messages = components["messages"]
    assert isinstance(messages, dict)
    messages["Problem"] = {
        "name": "problem",
        "payload": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    }
    publish = operations["publish"]
    lookup = operations["lookup"]
    prices = operations["prices"]
    assert isinstance(publish, dict)
    assert isinstance(lookup, dict)
    assert isinstance(prices, dict)
    publish["x-eazy-sdk.websocket"] = {
        "replay": {"kind": "if-unsent"},
        "signing": {"profile": "exchange-v1"},
        "completion": "complete",
    }
    lookup["x-eazy-sdk.websocket"] = {
        "errors": [{"$ref": "#/components/messages/Problem"}],
    }
    prices["x-eazy-sdk.websocket"] = {
        "kind": "subscribe",
        "discriminator": "subscribe",
        "recovery": {"kind": "sequence", "field": "sequence"},
    }

    ir = parse_asyncapi(document)
    lookup_ir = next(item for item in ir.operations if item.operation_id == "lookup")
    assert lookup_ir.error_messages[0].discriminator == "problem"

    package = generate_package(
        document,
        spec_path=tmp_path / "market.json",
        output_directory=tmp_path / "generated",
        package_name="market_extensions",
    )
    client_source = (package / "client.py").read_text(encoding="utf-8")
    protocol_source = (package / "protocol.py").read_text(encoding="utf-8")
    assert "ReplayIfUnsent()" in client_source
    assert "ErrorReply('problem', Problem)" in client_source
    assert "RecoverBySequence('sequence')" in client_source
    assert "ControlEvent('complete', ControlKind.COMPLETE)" in protocol_source
    assert "'profile': 'exchange-v1'" in protocol_source

    publish["x-eazy-sdk.websocket"] = {"protocol": "eazy_sdk.websocket:GraphqlTransportWsProtocol"}
    package = generate_package(
        document,
        spec_path=tmp_path / "graphql.json",
        output_directory=tmp_path / "graphql-generated",
        package_name="graphql_extensions",
    )
    protocol_source = (package / "protocol.py").read_text(encoding="utf-8")
    assert "from eazy_sdk.websocket import GraphqlTransportWsProtocol" in protocol_source
    assert "PROTOCOL = GraphqlTransportWsProtocol()" in protocol_source
