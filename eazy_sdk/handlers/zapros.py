"""Zapros client emitters and lifecycle wrappers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from zapros import (
    AsyncBaseHandler,
    BaseHandler,
    Headers,
    Multipart,
    Part,
    Response,
)
from zapros import (
    AsyncClient as ZaprosAsyncClient,
)
from zapros import (
    Client as ZaprosClient,
)

from eazy_sdk.handlers.profile import EmitOptions, TransportError
from eazy_sdk.request.logical import (
    ExactBodyInput,
    FormInput,
    JsonInput,
    MultipartInput,
    NoBodyInput,
    StreamBodyInput,
)
from eazy_sdk.request.prepared import BufferedBody, PreparedRequest
from eazy_sdk.response import NormalizedResponse


class BorrowedHandler(BaseHandler):
    def __init__(self, handler: BaseHandler) -> None:
        self.handler = handler

    def handle(self, request: Any) -> Response:
        return self.handler.handle(request)

    def close(self) -> None:
        return None


class BorrowedAsyncHandler(AsyncBaseHandler):
    def __init__(self, handler: AsyncBaseHandler) -> None:
        self.handler = handler

    async def ahandle(self, request: Any) -> Response:
        return await self.handler.ahandle(request)

    async def aclose(self) -> None:
        return None


class ZaprosSyncEmitter:
    def __init__(self, client: ZaprosClient) -> None:
        self.client = client

    def __call__(
        self, request: PreparedRequest, *, options: EmitOptions
    ) -> NormalizedResponse[Response]:
        try:
            request_call = cast(Any, self.client.request)
            response = request_call(
                request.method.decode("ascii"),
                request.url,
                headers=_headers(request),
                context=_context(options),
                **_body_kwargs(request),
            )
        except Exception as exc:
            raise TransportError("zapros", "emit", None, exc) from exc
        return _normalize(response)


class ZaprosAsyncEmitter:
    def __init__(self, client: ZaprosAsyncClient) -> None:
        self.client = client

    async def __call__(
        self, request: PreparedRequest, *, options: EmitOptions
    ) -> NormalizedResponse[Response]:
        try:
            request_call = cast(Any, self.client.request)
            response = await request_call(
                request.method.decode("ascii"),
                request.url,
                headers=_headers(request),
                context=_context(options),
                **_body_kwargs(request),
            )
        except Exception as exc:
            raise TransportError("zapros", "emit", None, exc) from exc
        return _normalize(response)


def _headers(request: PreparedRequest) -> Headers:
    return Headers(
        (field.name.decode("ascii"), field.value.decode("utf-8")) for field in request.headers
    )


def _context(options: EmitOptions) -> dict[str, object]:
    return {"timeouts": {"total": options.timeout}} if options.timeout is not None else {}


def _body_kwargs(request: PreparedRequest) -> dict[str, Any]:
    body = request.body_input
    if isinstance(body, NoBodyInput):
        return {}
    if isinstance(body, JsonInput):
        # Zapros uses ``None`` as its "json not supplied" sentinel.  Preserve JSON
        # null through the exact body path so the already prepared Content-Length
        # and the bytes received by the handler cannot diverge.
        if body.value is None:
            return {"body": _buffered(request)}
        return {"json": body.value}
    if isinstance(body, FormInput):
        return {"form": body.fields}
    if isinstance(body, MultipartInput):
        if any(part.headers for part in body.parts):
            return {"body": _buffered(request)}
        multipart = Multipart(body.boundary)
        for item in body.parts:
            part = Part.bytes(item.content)
            if item.filename is not None:
                part = part.file_name(item.filename)
            if item.content_type is not None:
                part = part.mime_type(item.content_type)
            multipart.part(item.name, part)
        return {"multipart": multipart}
    if isinstance(body, ExactBodyInput):
        return {"body": body.content}
    if isinstance(body, StreamBodyInput):
        return {"body": _stream(body)}
    raise TypeError(f"unsupported Zapros body input: {type(body).__name__}")


def _buffered(request: PreparedRequest) -> bytes:
    if not isinstance(request.body, BufferedBody):
        raise TypeError("exact request body is not buffered")
    return request.body.content


def _stream(body: StreamBodyInput) -> Iterator[bytes]:
    stream = body.factory()
    try:
        while chunk := stream.read(64 * 1024):
            yield chunk
    finally:
        stream.close()


def _normalize(response: Response) -> NormalizedResponse[Response]:
    request = response.request
    headers = [
        (name, value) for name in response.headers for value in response.headers.getall(name)
    ]
    return NormalizedResponse(
        response.status,
        str(request.url) if request is not None else "",
        request.method if request is not None else None,
        headers,
        response.read(),
        raw_response=response,
    )


__all__ = [
    "BorrowedAsyncHandler",
    "BorrowedHandler",
    "ZaprosAsyncEmitter",
    "ZaprosSyncEmitter",
]
