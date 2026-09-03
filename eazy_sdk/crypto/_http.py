"""HTTP lowering for common payload-crypto stages."""

from __future__ import annotations

import json
from dataclasses import replace

from eazy_sdk.request.logical import ExactBodyInput
from eazy_sdk.request.prepared import (
    BufferedBody,
    HeaderField,
    PreparedBodyView,
    ReplayableBodyStream,
    UnsignedPreparedRequest,
)
from eazy_sdk.response import NormalizedResponse
from eazy_sdk.response.normalized import cast_headers

from ._runtime import (
    CompiledPayloadCrypto,
    decrypt_bytes,
    decrypt_document,
    encrypt_bytes,
    encrypt_document,
)
from .core import (
    CryptoConfigurationError,
    CryptoOutput,
    CryptoOutputValue,
    CryptoStreamingUnsupportedError,
    CryptoValues,
    EncryptedMediaTypeMismatchError,
    FrozenValue,
    HttpCryptoContext,
    HttpEncrypted,
    freeze_value,
    thaw_value,
)


async def prepare_http_document(
    value: object,
    compiled: CompiledPayloadCrypto,
    *,
    context: HttpCryptoContext,
    outputs: list[CryptoOutputValue[object]] | None = None,
) -> object:
    if not compiled.outbound_fields:
        return value
    document = freeze_value(value)
    encrypted = await encrypt_document(
        document, compiled.outbound_fields, context=context, outputs=outputs
    )
    return thaw_value(encrypted)


async def protect_http_request(
    request: UnsignedPreparedRequest,
    compiled: CompiledPayloadCrypto,
    wire: HttpEncrypted,
    *,
    context: HttpCryptoContext,
    outputs: list[CryptoOutputValue[object]] | None = None,
) -> UnsignedPreparedRequest:
    outbound = compiled.profile.outbound
    if outbound is None:
        return request
    if outbound.encoded is None:
        headers = _apply_metadata_headers(request.headers, wire, outputs or [])
        view = replace(request.view, headers=headers) if request.view is not None else None
        return replace(request, headers=headers, view=view)
    if isinstance(request.body, ReplayableBodyStream):
        raise CryptoStreamingUnsupportedError(
            "whole-payload crypto does not support streaming bodies"
        )
    if not isinstance(request.body, BufferedBody):
        raise CryptoConfigurationError("HTTP encoded crypto requires a buffered body")
    content = await encrypt_bytes(
        request.body.content, outbound.encoded, context=context, outputs=outputs
    )
    body = BufferedBody(content, wire.content_type.encode("ascii"))
    headers = _replace_representation_headers(request.headers, wire.content_type, len(content))
    headers = _apply_metadata_headers(headers, wire, outputs or [])
    view = request.view
    if view is not None:
        view = replace(
            view,
            headers=headers,
            body=PreparedBodyView(
                content,
                wire.content_type,
                sensitive=view.body.sensitive,
            ),
        )
    return replace(
        request,
        headers=headers,
        body=body,
        view=view,
        body_input=ExactBodyInput(content, wire.content_type),
    )


async def unprotect_http_response[TRaw](
    response: NormalizedResponse[TRaw],
    compiled: CompiledPayloadCrypto,
    wire: HttpEncrypted,
    *,
    context: HttpCryptoContext,
) -> NormalizedResponse[TRaw]:
    inbound = compiled.profile.inbound
    if inbound is None:
        return response
    actual = response.content_type
    if response.status_code in wire.plaintext_statuses and actual != _base_media_type(
        wire.content_type
    ):
        return response
    context = _context_with_http_metadata(context, response, compiled, wire)
    content = response.body
    transformed = False
    effective_content_type = response.effective_content_type
    if inbound.encoded is not None:
        expected = _base_media_type(wire.content_type)
        if actual != expected:
            raise EncryptedMediaTypeMismatchError(
                f"expected encrypted Content-Type {expected!r}, got {actual!r}"
            )
        content = await decrypt_bytes(content, inbound.encoded, context=context)
        effective_content_type = wire.clear_content_type
        transformed = True
    if compiled.inbound_fields:
        clear_type = effective_content_type or actual
        if not _is_json_media_type(clear_type):
            raise EncryptedMediaTypeMismatchError(
                f"field decryption requires a JSON media type, got {clear_type!r}"
            )
        try:
            document: FrozenValue = freeze_value(json.loads(content))
        except Exception:
            raise EncryptedMediaTypeMismatchError(
                "decrypted payload is not a valid JSON document"
            ) from None
        decrypted = await decrypt_document(document, compiled.inbound_fields, context=context)
        content = json.dumps(
            thaw_value(decrypted),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        transformed = True
    if not transformed:
        return response
    return replace(
        response,
        body=content,
        wire_body=response.wire_body if response.wire_body is not None else response.body,
        wire_headers=(
            response.wire_headers if response.wire_headers is not None else response.headers
        ),
        effective_content_type=effective_content_type,
    )


def _replace_representation_headers(
    headers: tuple[HeaderField, ...],
    content_type: str,
    length: int,
) -> tuple[HeaderField, ...]:
    retained = tuple(
        header
        for header in headers
        if header.name.lower() not in {b"content-type", b"content-length"}
    )
    return (
        *retained,
        HeaderField(b"Content-Type", content_type.encode("ascii")),
        HeaderField(b"Content-Length", str(length).encode("ascii")),
    )


def _apply_metadata_headers(
    headers: tuple[HeaderField, ...],
    wire: HttpEncrypted,
    outputs: list[CryptoOutputValue[object]],
) -> tuple[HeaderField, ...]:
    values = {id(item.output): item.value for item in outputs}
    retained = tuple(
        header
        for header in headers
        if header.name.decode("ascii").casefold()
        not in {binding.name.casefold() for binding in wire.metadata}
    )
    applied: list[HeaderField] = []
    for binding in wire.metadata:
        if id(binding.output) not in values:
            continue
        applied.append(
            HeaderField(
                binding.name.encode("ascii"),
                _encode_http_metadata(values[id(binding.output)]).encode("ascii"),
            )
        )
    return (*retained, *applied)


def _context_with_http_metadata[TRaw](
    context: HttpCryptoContext,
    response: NormalizedResponse[TRaw],
    compiled: CompiledPayloadCrypto,
    wire: HttpEncrypted,
) -> HttpCryptoContext:
    headers = cast_headers(response.headers)
    values = list(context.values.items)
    inbound = compiled.profile.inbound
    required: set[int] = set()
    if inbound is not None:
        for inbound_field in inbound.fields:
            required.update(id(item) for item in inbound_field.metadata)
        if inbound.encoded is not None:
            required.update(id(item) for item in inbound.encoded.metadata)
    for binding in wire.metadata:
        if id(binding.output) not in required:
            continue
        raw = headers.get(binding.name)
        if raw is None:
            raise CryptoConfigurationError(f"missing crypto metadata header {binding.name!r}")
        values.append((binding.output, _decode_http_metadata(binding.output, raw)))
    return replace(context, values=CryptoValues(tuple(values)))


def _encode_http_metadata(value: object) -> str:
    if isinstance(value, str):
        encoded = value
    elif isinstance(value, bool):
        encoded = "true" if value else "false"
    elif isinstance(value, int | float):
        encoded = str(value)
    else:
        raise CryptoConfigurationError("HTTP crypto metadata must be a scalar value")
    if any(character in encoded for character in "\r\n"):
        raise CryptoConfigurationError("HTTP crypto metadata contains a line break")
    return encoded


def _decode_http_metadata(output: CryptoOutput[object], raw: str) -> object:
    annotation = getattr(output.validator, "annotation", str)
    try:
        if annotation is str:
            value: object = raw
        elif annotation is bool:
            if raw not in {"true", "false"}:
                raise ValueError
            value = raw == "true"
        elif annotation is int:
            value = int(raw)
        elif annotation is float:
            value = float(raw)
        else:
            raise CryptoConfigurationError(
                "HTTP crypto metadata supports str, bool, int and float outputs"
            )
        return output.validator(value)
    except (TypeError, ValueError):
        raise CryptoConfigurationError(f"invalid HTTP crypto metadata {output.name!r}") from None


def _base_media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _is_json_media_type(value: str | None) -> bool:
    if value is None or "/" not in value:
        return False
    subtype = _base_media_type(value).split("/", 1)[1]
    return subtype == "json" or subtype.endswith("+json")


__all__: list[str] = []
