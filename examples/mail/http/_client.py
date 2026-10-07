"""HTTP client adapter for the local teaching site; never included in docs pages."""

from __future__ import annotations

import httpx

from eazy_sdk import AsyncClient, Client, ClientConfig
from eazy_sdk.handlers.httpx import AsyncHttpxHandler, HttpxHandler

from examples.mail.site import MailSite, handle_httpx


def mail_client() -> Client:
    site = MailSite()
    raw = httpx.Client(
        transport=httpx.MockTransport(lambda request: handle_httpx(request, site)),
        headers={},
        cookies={},
    )
    return Client(handler=HttpxHandler(raw, owns_client=True))


def configured_mail_client(site: MailSite, config: ClientConfig) -> Client:
    raw = httpx.Client(
        transport=httpx.MockTransport(lambda request: handle_httpx(request, site)),
        headers={},
        cookies={},
    )
    return Client(handler=HttpxHandler(raw, owns_client=True), config=config)


def async_mail_client(site: MailSite) -> AsyncClient:
    raw = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: handle_httpx(request, site)),
        headers={},
        cookies={},
    )
    return AsyncClient(
        base_url="https://mail.example",
        handler=AsyncHttpxHandler(raw, owns_client=True),
    )


__all__ = ["async_mail_client", "configured_mail_client", "mail_client"]
