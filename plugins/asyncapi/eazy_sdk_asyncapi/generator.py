"""Deterministic thin AsyncWsApi package generation."""

from __future__ import annotations

import keyword
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .ir import AsyncAPIIR, MessageIR, OperationIR, parse_asyncapi


def generate_package(
    document: Mapping[str, Any],
    *,
    spec_path: Path,
    output_directory: Path,
    package_name: str,
) -> Path:
    del spec_path
    if not package_name.isidentifier() or keyword.iskeyword(package_name):
        raise ValueError("package_name must be a valid Python identifier")
    ir = parse_asyncapi(document)
    output_directory.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=f".{package_name}-", dir=output_directory.parent))
    temporary_output = temporary_root / package_name
    temporary_output.mkdir()
    files = {
        "__init__.py": _render_init(ir),
        "client.py": _render_client(ir),
        "models.py": _render_models(ir),
        "protocol.py": _render_protocol(ir),
    }
    try:
        for name, source in files.items():
            (temporary_output / name).write_text(source, encoding="utf-8", newline="\n")
        destination = output_directory / package_name
        if destination.exists():
            shutil.rmtree(destination)
        temporary_output.replace(destination)
        return destination
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def _render_init(ir: AsyncAPIIR) -> str:
    model_names = sorted({message.model_name for message in ir.messages})
    event_names = sorted(
        f"{_pascal(operation.operation_id)}Event"
        for operation in ir.operations
        if operation.kind == "event"
    )
    imports = ", ".join(model_names)
    exports = [
        "AsyncAPI",
        "CHANNELS",
        "PROTOCOL",
        "SERVERS",
        "SIGNING_REQUIREMENTS",
        *event_names,
        *model_names,
    ]
    if any(operation.crypto for operation in ir.operations):
        exports.insert(2, "crypto_registry")
    lines = [
        '"""Generated AsyncAPI WebSocket SDK."""',
        "",
        f"from .client import AsyncAPI{', ' if event_names else ''}{', '.join(event_names)}",
        f"from .models import {imports}" if imports else "",
        (
            "from .protocol import (CHANNELS, PROTOCOL, SERVERS, SIGNING_REQUIREMENTS, "
            "crypto_registry)"
            if any(operation.crypto for operation in ir.operations)
            else "from .protocol import CHANNELS, PROTOCOL, SERVERS, SIGNING_REQUIREMENTS"
        ),
        "",
        f"__all__ = {exports!r}",
        "",
    ]
    return "\n".join(lines)


def _render_models(ir: AsyncAPIIR) -> str:
    messages = _unique_messages(ir)
    lines = [
        '"""Generated AsyncAPI payload models."""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
    ]
    for message in messages:
        schema = message.payload_schema
        if schema.get("type", "object") == "object":
            lines.append("@dataclass(frozen=True, slots=True)")
            lines.append(f"class {message.model_name}:")
            properties = schema.get("properties", {})
            required_raw = schema.get("required", [])
            required = set(required_raw if isinstance(required_raw, list) else [])
            if not isinstance(properties, Mapping) or not properties:
                lines.append("    pass")
            else:
                ordered = sorted(
                    properties.items(),
                    key=lambda item: (item[0] not in required, item[0]),
                )
                for name, child in ordered:
                    annotation = _schema_annotation(child)
                    identifier = _identifier(name)
                    if name in required:
                        lines.append(f"    {identifier}: {annotation}")
                    else:
                        lines.append(f"    {identifier}: {annotation} | None = None")
        else:
            lines.append(f"type {message.model_name} = {_schema_annotation(schema)}")
        lines.append("")
    exports = [message.model_name for message in messages]
    lines.append(f"__all__ = {exports!r}")
    lines.append("")
    return "\n".join(lines)


def _render_protocol(ir: AsyncAPIIR) -> str:
    servers = {server.name: server.url for server in ir.servers}
    channels = {
        channel.name: {
            "address": channel.address,
            "parameters": channel.parameters,
        }
        for channel in ir.channels
    }
    protocol_imports = {
        value
        for operation in ir.operations
        if isinstance((value := operation.extension.get("protocol")), str)
    }
    if len(protocol_imports) > 1:
        raise ValueError("generated package cannot combine multiple protocol imports")
    completions = tuple(
        dict.fromkeys(
            value
            for operation in ir.operations
            if isinstance((value := operation.extension.get("completion")), str)
        )
    )
    signing = {
        operation.operation_id: dict(value)
        for operation in ir.operations
        if isinstance((value := operation.extension.get("signing")), Mapping)
    }
    has_crypto = any(operation.crypto is not None for operation in ir.operations)
    lines = ['"""Generated AsyncAPI protocol constants."""', ""]
    if has_crypto:
        lines.extend(
            [
                "from collections.abc import Mapping",
                "",
                "from eazy_sdk.crypto import (",
                "    CryptoDirection,",
                "    CryptoRegistry,",
                "    CryptoRule,",
                "    PayloadCrypto,",
                "    websocket_crypto_scope,",
                "    websocket_encrypted,",
                ")",
                "",
            ]
        )
    if protocol_imports:
        module, symbol = next(iter(protocol_imports)).split(":", 1)
        lines.extend([f"from {module} import {symbol}", ""])
        protocol_expression = f"{symbol}()"
    else:
        lines.extend(
            [
                "from eazy_sdk.websocket import (",
                "    ControlEvent,",
                "    ControlKind,",
                "    JsonEventProtocol,",
                ")",
                "",
            ]
        )
        controls = ", ".join(
            f"ControlEvent({value!r}, ControlKind.COMPLETE)" for value in completions
        )
        protocol_expression = "\n".join(
            [
                "JsonEventProtocol(",
                '    event_field="type",',
                '    payload_field="data",',
                '    correlation_field="id",',
                f"    controls=({controls}{',' if controls else ''}),",
                ")",
            ]
        )
    lines.extend(
        [
            f"SERVERS: dict[str, str] = {servers!r}",
            f"CHANNELS: dict[str, dict[str, object]] = {channels!r}",
            f"SIGNING_REQUIREMENTS: dict[str, object] = {signing!r}",
            f"PROTOCOL = {protocol_expression}",
            "",
        ]
    )
    if has_crypto:
        lines.extend(_render_crypto_registry(ir))
    exports = ["CHANNELS", "PROTOCOL", "SERVERS", "SIGNING_REQUIREMENTS"]
    if has_crypto:
        exports.append("crypto_registry")
    lines.extend(["", f"__all__ = {exports!r}", ""])
    return "\n".join(lines)


def _render_crypto_registry(ir: AsyncAPIIR) -> list[str]:
    operations = tuple(operation for operation in ir.operations if operation.crypto is not None)
    required = tuple(
        dict.fromkeys(
            str(operation.crypto["profile"])
            for operation in operations
            if operation.crypto
        )
    )
    rules: list[str] = []
    for operation in operations:
        crypto = operation.crypto
        assert crypto is not None
        direction = str(crypto["direction"])
        directions = {
            "outbound": ("CryptoDirection.OUTBOUND",),
            "inbound": ("CryptoDirection.INBOUND",),
            "bidirectional": (
                "CryptoDirection.OUTBOUND",
                "CryptoDirection.INBOUND",
            ),
        }[direction]
        trailing_comma = "," if len(directions) == 1 else ""
        direction_expression = (
            f"frozenset(({', '.join(directions)}{trailing_comma}))"
        )
        wire = cast(Mapping[str, Any], crypto["wire"])
        rules.extend(
            [
                "        CryptoRule(",
                f"            profile=profiles[{crypto['profile']!r}],",
                "            scope=websocket_crypto_scope(",
                f"                operation_ids=({operation.operation_id!r},),",
                "            ),",
                "            wire=websocket_encrypted(",
                f"                frame_kind={wire['frameKind']!r},",
                f"                text_safe={wire['textSafe']!r},",
                f"                clear_frame_kind={wire['clearFrameKind']!r},",
                "            ),",
                f"            directions={direction_expression},",
                "        ),",
            ]
        )
    return [
        "",
        "",
        "def crypto_registry(profiles: Mapping[str, PayloadCrypto]) -> CryptoRegistry:",
        f"    required = frozenset({required!r})",
        "    missing = required.difference(profiles)",
        "    if missing:",
        "        raise KeyError(f'missing crypto profiles: {sorted(missing)!r}')",
        "    return CryptoRegistry(",
        "        (",
        *rules,
        "        )",
        "    )",
    ]


def _render_client(ir: AsyncAPIIR) -> str:
    models = sorted({message.model_name for message in ir.messages})
    lines = [
        '"""Generated thin AsyncWsApi declarations."""',
        "",
        "from __future__ import annotations",
        "",
        "from eazy_sdk.websocket import (",
        "    AsyncWsApi,",
        "    EmptyPayload,",
        "    ErrorReply,",
        "    Event,",
        "    JsonPayload,",
        "    Message,",
        "    Messages,",
        "    NeverResubscribe,",
        "    RecoverBySequence,",
        "    RecoverByToken,",
        "    ReplayIfUnsent,",
        "    ResubscribeFromStart,",
        "    Replies,",
        "    Subscription,",
        "    SuccessReply,",
        "    ws,",
        ")",
        "",
        f"from .models import {', '.join(models)}" if models else "",
        "",
    ]
    event_exports: list[str] = []
    for operation in ir.operations:
        if operation.kind != "event":
            continue
        model = operation.messages[0].model_name
        alias = f"{_pascal(operation.operation_id)}Event"
        event_exports.append(alias)
        lines.append(f"type {alias} = Event[{model}]")
    if event_exports:
        lines.append("")
    lines.extend(["class AsyncAPI(AsyncWsApi):"])
    executable = [operation for operation in ir.operations if operation.kind != "event"]
    if not executable:
        lines.append("    pass")
    for operation in executable:
        lines.extend(_render_operation(operation))
    lines.append("")
    lines.append(f"__all__ = {['AsyncAPI', *event_exports]!r}")
    lines.append("")
    return "\n".join(lines)


def _render_operation(operation: OperationIR) -> list[str]:
    name = _identifier(operation.operation_id)
    if operation.kind in {"send", "call"}:
        request = operation.messages[0]
        parameters = _model_parameters(request)
        replay = _render_replay(operation)
        decorator = (
            f"    @ws.send({request.discriminator!r}, operation_id={operation.operation_id!r}, "
            f"payload=JsonPayload({request.model_name}){replay})"
        )
        return_type = "None"
        if operation.kind == "call":
            reply_cases = ", ".join(
                f"SuccessReply({reply.discriminator!r}, {reply.model_name})"
                for reply in operation.reply_messages
            )
            error_cases = ", ".join(
                f"ErrorReply({error.discriminator!r}, {error.model_name})"
                for error in operation.error_messages
            )
            decorator = (
                f"    @ws.call({request.discriminator!r}, operation_id={operation.operation_id!r}, "
                f"payload=JsonPayload({request.model_name}), "
                f"replies=Replies(success=({reply_cases},), "
                f"errors=({error_cases}{',' if error_cases else ''})){replay})"
            )
            return_type = " | ".join(
                dict.fromkeys(reply.model_name for reply in operation.reply_messages)
            )
        return [
            "",
            decorator,
            f"    async def {name}(self{parameters}) -> {return_type}:",
            "        raise NotImplementedError",
        ]
    if operation.kind == "subscribe":
        message_cases = ", ".join(
            f"Message({message.discriminator!r}, {message.model_name})"
            for message in operation.messages
        )
        return_type = " | ".join(
            dict.fromkeys(message.model_name for message in operation.messages)
        )
        recovery = _render_recovery(operation)
        return [
            "",
            (
                f"    @ws.subscribe({operation.discriminator!r}, "
                f"operation_id={operation.operation_id!r}, payload=EmptyPayload(), "
                f"messages=Messages(({message_cases},)){recovery})"
            ),
            f"    async def {name}(self) -> Subscription[{return_type}]:",
            "        raise NotImplementedError",
        ]
    raise AssertionError(f"unsupported generated operation kind: {operation.kind}")


def _render_replay(operation: OperationIR) -> str:
    replay = operation.extension.get("replay")
    if not isinstance(replay, Mapping) or replay.get("kind") != "if-unsent":
        return ""
    return ", replay=ReplayIfUnsent()"


def _render_recovery(operation: OperationIR) -> str:
    recovery = operation.extension.get("recovery")
    if not isinstance(recovery, Mapping):
        return ""
    kind = recovery.get("kind")
    if kind == "from-start":
        return ", resubscribe=ResubscribeFromStart()"
    if kind == "sequence":
        return f", resubscribe=RecoverBySequence({recovery['field']!r})"
    if kind == "token":
        return f", resubscribe=RecoverByToken({recovery['field']!r})"
    if kind == "never":
        return ", resubscribe=NeverResubscribe()"
    return ""


def _model_parameters(message: MessageIR) -> str:
    schema = message.payload_schema
    if schema.get("type", "object") != "object":
        return f", value: {_schema_annotation(schema)}"
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return ""
    required_raw = schema.get("required", [])
    required = set(required_raw if isinstance(required_raw, list) else [])
    ordered = sorted(properties.items(), key=lambda item: (item[0] not in required, item[0]))
    rendered: list[str] = []
    for name, child in ordered:
        annotation = _schema_annotation(child)
        if name in required:
            rendered.append(f"{_identifier(name)}: {annotation}")
        else:
            rendered.append(f"{_identifier(name)}: {annotation} | None = None")
    return "" if not rendered else ", " + ", ".join(rendered)


def _schema_annotation(raw: object) -> str:
    if not isinstance(raw, Mapping):
        return "object"
    schema_type = raw.get("type", "object")
    if schema_type == "string":
        return "str"
    if schema_type == "integer":
        return "int"
    if schema_type == "number":
        return "float"
    if schema_type == "boolean":
        return "bool"
    if schema_type == "array":
        return f"list[{_schema_annotation(raw.get('items', {}))}]"
    return "dict[str, object]"


def _unique_messages(ir: AsyncAPIIR) -> tuple[MessageIR, ...]:
    seen: set[int] = set()
    result: list[MessageIR] = []
    for message in ir.messages:
        if id(message) in seen:
            continue
        seen.add(id(message))
        result.append(message)
    return tuple(result)


def _identifier(value: str) -> str:
    words = _words(value)
    result = "_".join(word.casefold() for word in words) or "operation"
    if result[0].isdigit():
        result = f"value_{result}"
    if keyword.iskeyword(result):
        result += "_"
    return result


def _pascal(value: str) -> str:
    words = _words(value)
    result = "".join(word[:1].upper() + word[1:] for word in words) or "Anonymous"
    return f"Generated{result}" if result[0].isdigit() else result


def _words(value: str) -> list[str]:
    normalized = "".join(character if character.isalnum() else " " for character in value)
    return normalized.split()


__all__ = ["generate_package"]
