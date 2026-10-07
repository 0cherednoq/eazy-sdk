"""HTTP client adapter for the local teaching site; never included in docs pages."""

from __future__ import annotations

import httpx

from eazy_sdk import Client
from eazy_sdk.handlers.httpx import HttpxHandler

from examples.mail.site import handle_httpx


def mail_client() -> Client:
    raw = httpx.Client(
        transport=httpx.MockTransport(handle_httpx),
        headers={},
        cookies={},
    )
    return Client(handler=HttpxHandler(raw, owns_client=True))


__all__ = ["mail_client"]
