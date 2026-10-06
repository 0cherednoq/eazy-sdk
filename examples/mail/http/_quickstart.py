"""Local client setup shared by the three quickstart model variants."""

from __future__ import annotations

import httpx

from eazy_sdk import Client
from eazy_sdk.handlers.httpx import HttpxHandler

from examples.mail.site import handle_httpx


def quickstart_client() -> Client:
    raw = httpx.Client(
        base_url="https://mail.example",
        transport=httpx.MockTransport(handle_httpx),
        headers={},
        cookies={},
    )
    return Client(handler=HttpxHandler(raw, owns_client=True))


__all__ = ["quickstart_client"]
