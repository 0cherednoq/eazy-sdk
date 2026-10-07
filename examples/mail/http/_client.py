"""HTTP client adapter for the local teaching site; never included in docs pages."""

from __future__ import annotations

import httpx

from eazy_sdk import Client, ClientConfig
from eazy_sdk.handlers.httpx import HttpxHandler

from examples.mail.site import MailSite, handle_httpx


def mail_client() -> Client:
    raw = httpx.Client(
        transport=httpx.MockTransport(handle_httpx),
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


__all__ = ["configured_mail_client", "mail_client"]
