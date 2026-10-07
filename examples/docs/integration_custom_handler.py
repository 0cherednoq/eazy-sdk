"""Runnable custom Zapros handler example for the documentation."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from urllib.parse import urlsplit

from zapros import BaseHandler, Request, Response

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op

from .guide_site import GuideSite, guide_origin


# docs:integration-custom-handler:start
# examples/docs/integration_custom_handler.py
class UrllibHandler(BaseHandler):
    def handle(self, request: Request) -> Response:
        target = str(request.url)
        if urlsplit(target).scheme not in {"http", "https"}:
            raise ValueError("UrllibHandler accepts only HTTP URLs")
        native_request = UrlRequest(  # noqa: S310 - the scheme is checked above
            target,
            data=request.body if isinstance(request.body, bytes) else None,
            headers=dict(request.headers.items()),
            method=request.method,
        )
        with urlopen(native_request) as native_response:  # noqa: S310
            return Response(
                native_response.status,
                native_response.headers.items(),
                content=native_response.read(),
                request=request,
            )


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    subject: str


@dataclass(frozen=True, slots=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/42")


class MessageApi(SyncApi):
    message = op(GetMessage)


def load_message(base_url: str) -> Message:
    with Client(base_url=base_url, handler=UrllibHandler()) as client:
        return MessageApi(client).message()
# docs:integration-custom-handler:end


def main() -> None:
    with guide_origin(GuideSite()) as base_url:
        message = load_message(base_url)
    print(f"message: {message.id} {message.subject}")


if __name__ == "__main__":
    main()
