"""Validated, identity-preserving AsyncAPI 3.0 WebSocket intermediate representation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Never, cast


class AsyncApiDiagnosticError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        operation_id: str | None,
        pointer: str,
        reference_chain: tuple[str, ...] = (),
    ) -> None:
        self.operation_id = operation_id
        self.pointer = pointer
        self.reference_chain = reference_chain
        label = operation_id or "document"
        chain = f" via {' -> '.join(reference_chain)}" if reference_chain else ""
        super().__init__(f"{label} at {pointer}{chain}: {message}")


@dataclass(frozen=True, slots=True)
class ServerIR:
    name: str
    protocol: str
    host: str
    pathname: str
    url: str
    pointer: str


@dataclass(frozen=True, slots=True)
class MessageIR:
    name: str
    model_name: str
    discriminator: str
    payload_schema: Mapping[str, Any]
    correlation_location: str | None
    pointer: str


@dataclass(frozen=True, slots=True)
class ChannelIR:
    name: str
    address: str
    parameters: tuple[str, ...]
    messages: tuple[MessageIR, ...]
    pointer: str


@dataclass(frozen=True, slots=True)
class OperationIR:
    operation_id: str
    action: str
    kind: str
    channel: ChannelIR
    messages: tuple[MessageIR, ...]
    reply_messages: tuple[MessageIR, ...]
    error_messages: tuple[MessageIR, ...]
    discriminator: str
    extension: Mapping[str, Any]
    crypto: Mapping[str, Any] | None
    pointer: str


@dataclass(frozen=True, slots=True)
class AsyncAPIIR:
    title: str
    version: str
    servers: tuple[ServerIR, ...]
    channels: tuple[ChannelIR, ...]
    messages: tuple[MessageIR, ...]
    operations: tuple[OperationIR, ...]


class _Parser:
    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = document
        self.message_cache: dict[str, MessageIR] = {}
        self.channel_cache: dict[str, ChannelIR] = {}

    def parse(self) -> AsyncAPIIR:
        version = self.document.get("asyncapi")
        if not isinstance(version, str) or not version.startswith("3.0."):
            self.fail("only AsyncAPI 3.0.x is supported", pointer="#/asyncapi")
        info = self.mapping(self.document.get("info"), "#/info")
        title = info.get("title")
        api_version = info.get("version")
        if not isinstance(title, str) or not isinstance(api_version, str):
            self.fail("info.title and info.version must be strings", pointer="#/info")

        components = self.mapping(self.document.get("components", {}), "#/components")
        component_messages = self.mapping(components.get("messages", {}), "#/components/messages")
        for name, node in component_messages.items():
            self.message(node, f"#/components/messages/{_escape(name)}", name=name)

        channels_node = self.mapping(self.document.get("channels", {}), "#/channels")
        channels = tuple(
            self.channel(node, f"#/channels/{_escape(name)}", name=name)
            for name, node in channels_node.items()
        )
        servers = self.servers()
        operations = self.operations()
        unique_messages = tuple(dict.fromkeys(map(id, self.message_cache.values())))
        messages_by_id = {id(message): message for message in self.message_cache.values()}
        return AsyncAPIIR(
            title,
            api_version,
            servers,
            channels,
            tuple(messages_by_id[identity] for identity in unique_messages),
            operations,
        )

    def servers(self) -> tuple[ServerIR, ...]:
        servers = self.mapping(self.document.get("servers", {}), "#/servers")
        result: list[ServerIR] = []
        for name, raw in servers.items():
            pointer = f"#/servers/{_escape(name)}"
            node = self.mapping(raw, pointer)
            self.reject_bindings(node, pointer=pointer)
            protocol = node.get("protocol")
            host = node.get("host")
            pathname = node.get("pathname", "")
            if protocol not in {"ws", "wss"}:
                self.fail("only ws and wss server protocols are supported", pointer=pointer)
            if not isinstance(host, str) or not host:
                self.fail("server.host must be a non-empty string", pointer=pointer)
            if not isinstance(pathname, str):
                self.fail("server.pathname must be a string", pointer=pointer)
            normalized_path = (
                pathname if not pathname or pathname.startswith("/") else f"/{pathname}"
            )
            result.append(
                ServerIR(
                    name,
                    cast(str, protocol),
                    host,
                    normalized_path,
                    f"{protocol}://{host}{normalized_path}",
                    pointer,
                )
            )
        return tuple(result)

    def channel(self, raw: object, pointer: str, *, name: str) -> ChannelIR:
        if pointer in self.channel_cache:
            return self.channel_cache[pointer]
        target, target_pointer, chain = self.resolve(raw, pointer)
        if target_pointer in self.channel_cache:
            return self.channel_cache[target_pointer]
        node = self.mapping(target, target_pointer)
        self.reject_bindings(node, pointer=f"{target_pointer}/bindings", chain=chain)
        address = node.get("address")
        if not isinstance(address, str) or not address:
            self.fail("channel.address must be a non-empty string", pointer=target_pointer)
        parameters_node = self.mapping(node.get("parameters", {}), f"{target_pointer}/parameters")
        messages_node = self.mapping(node.get("messages", {}), f"{target_pointer}/messages")
        messages = tuple(
            self.message(value, f"{target_pointer}/messages/{_escape(key)}", name=key)
            for key, value in messages_node.items()
        )
        channel = ChannelIR(
            name,
            address,
            tuple(parameters_node),
            messages,
            target_pointer,
        )
        self.channel_cache[target_pointer] = channel
        self.channel_cache[pointer] = channel
        return channel

    def message(self, raw: object, pointer: str, *, name: str) -> MessageIR:
        if pointer in self.message_cache:
            return self.message_cache[pointer]
        target, target_pointer, _ = self.resolve(raw, pointer)
        if target_pointer in self.message_cache:
            message = self.message_cache[target_pointer]
            self.message_cache[pointer] = message
            return message
        node = self.mapping(target, target_pointer)
        payload = self.mapping(node.get("payload", {}), f"{target_pointer}/payload")
        declared_name = node.get("name", name)
        if not isinstance(declared_name, str) or not declared_name:
            self.fail("message.name must be a non-empty string", pointer=target_pointer)
        correlation = node.get("correlationId")
        correlation_location: str | None = None
        if correlation is not None:
            correlation_node = self.mapping(correlation, f"{target_pointer}/correlationId")
            location = correlation_node.get("location")
            if not isinstance(location, str) or not location:
                self.fail(
                    "correlationId.location must be a non-empty string",
                    pointer=f"{target_pointer}/correlationId/location",
                )
            correlation_location = location
        message = MessageIR(
            name,
            _pascal(name),
            declared_name,
            payload,
            correlation_location,
            target_pointer,
        )
        self.message_cache[target_pointer] = message
        self.message_cache[pointer] = message
        return message

    def operations(self) -> tuple[OperationIR, ...]:
        operations = self.mapping(self.document.get("operations", {}), "#/operations")
        result: list[OperationIR] = []
        for operation_id, raw in operations.items():
            pointer = f"#/operations/{_escape(operation_id)}"
            node = self.mapping(raw, pointer)
            self.reject_bindings(
                node,
                pointer=f"{pointer}/bindings",
                operation_id=operation_id,
                chain=(pointer,),
            )
            action = node.get("action")
            if action not in {"send", "receive"}:
                self.fail(
                    "operation.action must be send or receive",
                    operation_id=operation_id,
                    pointer=f"{pointer}/action",
                    chain=(pointer,),
                )
            channel_raw = node.get("channel")
            channel_target, channel_pointer, _ = self.resolve(channel_raw, f"{pointer}/channel")
            channel_name = channel_pointer.rsplit("/", 1)[-1]
            channel = self.channel(channel_target, channel_pointer, name=channel_name)
            messages = self.operation_messages(
                node.get("messages", ()),
                f"{pointer}/messages",
                operation_id,
            )
            reply_messages: tuple[MessageIR, ...] = ()
            if "reply" in node:
                reply = self.mapping(node["reply"], f"{pointer}/reply")
                reply_messages = self.operation_messages(
                    reply.get("messages", ()),
                    f"{pointer}/reply/messages",
                    operation_id,
                )
            for message in (*messages, *reply_messages):
                self.validate_schema(
                    message.payload_schema,
                    f"{message.pointer}/payload",
                    operation_id=operation_id,
                    chain=(pointer, message.pointer),
                )
            extension = self.extension(node, pointer, operation_id)
            crypto = self.crypto_extension(node, pointer, operation_id)
            error_messages = self.extension_messages(
                extension.get("errors", ()),
                f"{pointer}/x-eazy-sdk.websocket/errors",
                operation_id,
            )
            for message in error_messages:
                self.validate_schema(
                    message.payload_schema,
                    f"{message.pointer}/payload",
                    operation_id=operation_id,
                    chain=(pointer, message.pointer),
                )
            declared_kind = extension.get("kind")
            if declared_kind is not None and declared_kind != "subscribe":
                self.fail(
                    "x-eazy-sdk.websocket.kind must be subscribe when present",
                    operation_id=operation_id,
                    pointer=f"{pointer}/x-eazy-sdk.websocket/kind",
                    chain=(pointer,),
                )
            if action == "send":
                kind = "call" if reply_messages else "send"
            else:
                kind = "subscribe" if declared_kind == "subscribe" else "event"
            if kind in {"send", "call"} and len(messages) != 1:
                self.fail(
                    "send/call operations require exactly one message",
                    operation_id=operation_id,
                    pointer=f"{pointer}/messages",
                    chain=(pointer,),
                )
            if kind == "call" and not reply_messages:
                self.fail(
                    "call operation requires reply messages",
                    operation_id=operation_id,
                    pointer=f"{pointer}/reply/messages",
                    chain=(pointer,),
                )
            discriminator = cast(str, extension.get("discriminator", operation_id))
            result.append(
                OperationIR(
                    operation_id,
                    cast(str, action),
                    kind,
                    channel,
                    messages,
                    reply_messages,
                    error_messages,
                    discriminator,
                    extension,
                    crypto,
                    pointer,
                )
            )
        return tuple(result)

    def operation_messages(
        self,
        raw: object,
        pointer: str,
        operation_id: str,
    ) -> tuple[MessageIR, ...]:
        if not isinstance(raw, list | tuple):
            self.fail(
                "operation messages must be an array",
                operation_id=operation_id,
                pointer=pointer,
            )
        return tuple(
            self.message(value, f"{pointer}/{index}", name=f"{operation_id}_{index}")
            for index, value in enumerate(raw)
        )

    def extension(
        self,
        node: Mapping[str, Any],
        pointer: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        raw = node.get("x-eazy-sdk.websocket", {})
        extension = self.mapping(raw, f"{pointer}/x-eazy-sdk.websocket")
        allowed = {
            "kind",
            "discriminator",
            "completion",
            "errors",
            "recovery",
            "replay",
            "signing",
            "protocol",
        }
        unknown = set(extension) - allowed
        if unknown:
            self.fail(
                f"unknown canonical WebSocket extension fields: {sorted(unknown)!r}",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk.websocket",
                chain=(pointer,),
            )
        discriminator = extension.get("discriminator")
        if discriminator is not None and (not isinstance(discriminator, str) or not discriminator):
            self.fail(
                "extension discriminator must be a non-empty string",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk.websocket/discriminator",
            )
        completion = extension.get("completion")
        if completion is not None and (not isinstance(completion, str) or not completion):
            self.fail(
                "extension completion must be a non-empty string",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk.websocket/completion",
            )
        protocol = extension.get("protocol")
        if protocol is not None and (
            not isinstance(protocol, str)
            or protocol.count(":") != 1
            or not all(protocol.split(":"))
        ):
            self.fail(
                "extension protocol must use module:Symbol syntax",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk.websocket/protocol",
            )
        self.validate_policy_extension(extension, pointer, operation_id)
        return extension

    def extension_messages(
        self,
        raw: object,
        pointer: str,
        operation_id: str,
    ) -> tuple[MessageIR, ...]:
        if raw in (None, ()):
            return ()
        if not isinstance(raw, list | tuple):
            self.fail(
                "extension errors must be an array of message references",
                operation_id=operation_id,
                pointer=pointer,
            )
        return tuple(
            self.message(value, f"{pointer}/{index}", name=f"{operation_id}_error_{index}")
            for index, value in enumerate(raw)
        )

    def crypto_extension(
        self,
        node: Mapping[str, Any],
        pointer: str,
        operation_id: str,
    ) -> Mapping[str, Any] | None:
        raw = node.get("x-eazy-sdk-crypto")
        if raw is None:
            return None
        crypto = self.mapping(raw, f"{pointer}/x-eazy-sdk-crypto")
        unknown = set(crypto) - {"profile", "direction", "wire"}
        if unknown:
            self.fail(
                f"unknown crypto fields: {sorted(unknown)!r}",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk-crypto",
            )
        profile = crypto.get("profile")
        if not isinstance(profile, str) or not profile:
            self.fail(
                "crypto profile must be a non-empty string",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk-crypto/profile",
            )
        direction = crypto.get("direction", "bidirectional")
        if direction not in {"outbound", "inbound", "bidirectional"}:
            self.fail(
                "crypto direction must be outbound, inbound or bidirectional",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk-crypto/direction",
            )
        wire = self.mapping(crypto.get("wire", {}), f"{pointer}/x-eazy-sdk-crypto/wire")
        wire_unknown = set(wire) - {"frameKind", "textSafe", "clearFrameKind"}
        if wire_unknown:
            self.fail(
                f"unknown crypto wire fields: {sorted(wire_unknown)!r}",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk-crypto/wire",
            )
        frame_kind = wire.get("frameKind", "binary")
        clear_frame_kind = wire.get("clearFrameKind", "text")
        text_safe = wire.get("textSafe", False)
        if frame_kind not in {"binary", "text"}:
            self.fail(
                "frameKind must be binary or text",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk-crypto/wire/frameKind",
            )
        if clear_frame_kind not in {"binary", "text"}:
            self.fail(
                "clearFrameKind must be binary or text",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk-crypto/wire/clearFrameKind",
            )
        if not isinstance(text_safe, bool):
            self.fail(
                "textSafe must be a boolean",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk-crypto/wire/textSafe",
            )
        if frame_kind == "text" and not text_safe:
            self.fail(
                "text encrypted frames require textSafe=true",
                operation_id=operation_id,
                pointer=f"{pointer}/x-eazy-sdk-crypto/wire/textSafe",
            )
        return {
            "profile": profile,
            "direction": direction,
            "wire": {
                "frameKind": frame_kind,
                "textSafe": text_safe,
                "clearFrameKind": clear_frame_kind,
            },
        }

    def validate_policy_extension(
        self,
        extension: Mapping[str, Any],
        pointer: str,
        operation_id: str,
    ) -> None:
        replay = extension.get("replay")
        if replay is not None:
            replay_node = self.mapping(replay, f"{pointer}/x-eazy-sdk.websocket/replay")
            if replay_node.get("kind") not in {"never", "if-unsent"}:
                self.fail(
                    "replay.kind must be never or if-unsent",
                    operation_id=operation_id,
                    pointer=f"{pointer}/x-eazy-sdk.websocket/replay/kind",
                )
        recovery = extension.get("recovery")
        if recovery is not None:
            recovery_node = self.mapping(recovery, f"{pointer}/x-eazy-sdk.websocket/recovery")
            kind = recovery_node.get("kind")
            if kind not in {"never", "from-start", "sequence", "token"}:
                self.fail(
                    "recovery.kind must be never, from-start, sequence or token",
                    operation_id=operation_id,
                    pointer=f"{pointer}/x-eazy-sdk.websocket/recovery/kind",
                )
            if kind in {"sequence", "token"} and (
                not isinstance(recovery_node.get("field"), str) or not recovery_node.get("field")
            ):
                self.fail(
                    "sequence/token recovery requires a non-empty field",
                    operation_id=operation_id,
                    pointer=f"{pointer}/x-eazy-sdk.websocket/recovery/field",
                )
        signing = extension.get("signing")
        if signing is not None:
            self.mapping(signing, f"{pointer}/x-eazy-sdk.websocket/signing")

    def validate_schema(
        self,
        schema: Mapping[str, Any],
        pointer: str,
        *,
        operation_id: str | None = None,
        chain: tuple[str, ...] = (),
    ) -> None:
        schema_type = schema.get("type", "object")
        if schema_type not in {"object", "array", "string", "integer", "number", "boolean"}:
            self.fail(
                f"unsupported JSON Schema type: {schema_type!r}",
                operation_id=operation_id,
                pointer=pointer,
                chain=chain,
            )
        if "format" in schema:
            self.fail(
                f"unsupported JSON Schema format: {schema['format']!r}",
                operation_id=operation_id,
                pointer=f"{pointer}/format",
                chain=chain,
            )
        if schema_type == "object":
            properties = self.mapping(schema.get("properties", {}), f"{pointer}/properties")
            for name, child in properties.items():
                self.validate_schema(
                    self.mapping(child, f"{pointer}/properties/{_escape(name)}"),
                    f"{pointer}/properties/{_escape(name)}",
                    operation_id=operation_id,
                    chain=chain,
                )
        elif schema_type == "array":
            self.validate_schema(
                self.mapping(schema.get("items", {}), f"{pointer}/items"),
                f"{pointer}/items",
                operation_id=operation_id,
                chain=chain,
            )

    def resolve(
        self,
        raw: object,
        pointer: str,
    ) -> tuple[object, str, tuple[str, ...]]:
        current = raw
        current_pointer = pointer
        chain: list[str] = []
        seen: set[str] = set()
        while True:
            node = self.mapping(current, current_pointer)
            reference = node.get("$ref")
            if reference is None:
                return node, current_pointer, tuple(chain)
            if not isinstance(reference, str) or not reference.startswith("#/"):
                self.fail(
                    "only local JSON Pointer references are supported",
                    pointer=current_pointer,
                    chain=tuple(chain),
                )
            if reference in seen:
                self.fail(
                    "cyclic reference chain",
                    pointer=reference,
                    chain=tuple((*chain, reference)),
                )
            seen.add(reference)
            chain.extend((current_pointer, reference))
            current = self.lookup(reference)
            current_pointer = reference

    def lookup(self, pointer: str) -> object:
        current: object = self.document
        for component in pointer.removeprefix("#/").split("/"):
            key = component.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or key not in current:
                self.fail("reference target does not exist", pointer=pointer)
            current = current[key]
        return current

    def reject_bindings(
        self,
        node: Mapping[str, Any],
        *,
        pointer: str,
        operation_id: str | None = None,
        chain: tuple[str, ...] = (),
    ) -> None:
        if node.get("bindings"):
            self.fail(
                "protocol bindings are not supported",
                operation_id=operation_id,
                pointer=pointer,
                chain=chain,
            )

    def mapping(self, raw: object, pointer: str) -> Mapping[str, Any]:
        if not isinstance(raw, Mapping):
            self.fail("expected an object", pointer=pointer)
        return cast(Mapping[str, Any], raw)

    def fail(
        self,
        message: str,
        *,
        pointer: str,
        operation_id: str | None = None,
        chain: tuple[str, ...] = (),
    ) -> Never:
        raise AsyncApiDiagnosticError(
            message,
            operation_id=operation_id,
            pointer=pointer,
            reference_chain=chain,
        )


def parse_asyncapi(document: Mapping[str, Any]) -> AsyncAPIIR:
    return _Parser(document).parse()


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _pascal(value: str) -> str:
    parts = [part for part in _words(value) if part]
    result = "".join(part[:1].upper() + part[1:] for part in parts) or "Anonymous"
    return f"Model{result}" if result[:1].isdigit() else result


def _words(value: str) -> list[str]:
    normalized = "".join(character if character.isalnum() else " " for character in value)
    return normalized.split()


__all__ = [
    "AsyncAPIIR",
    "AsyncApiDiagnosticError",
    "ChannelIR",
    "MessageIR",
    "OperationIR",
    "ServerIR",
    "parse_asyncapi",
]
