"""Local HTTP boundary for exercising the real curl_cffi adapter."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlsplit

from .app import MailSite, SiteRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _handler(site: MailSite) -> type[BaseHTTPRequestHandler]:
    class MailHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle(self) -> None:
            # Browsers may drop an idle keep-alive socket while the local origin stops.
            with suppress(ConnectionResetError):
                super().handle()

        def do_GET(self) -> None:
            self._serve()

        def do_POST(self) -> None:
            self._serve()

        def _serve(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            parsed = urlsplit(self.path)
            response = site(
                SiteRequest(
                    method=self.command,
                    path=parsed.path,
                    query=tuple(parse_qsl(parsed.query, keep_blank_values=True)),
                    headers=tuple(self.headers.items()),
                    body=self.rfile.read(length),
                )
            )
            self.send_response(response.status)
            for name, value in response.headers:
                self.send_header(name, value)
            self.send_header("content-length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            _ = format, args

    return MailHandler


@asynccontextmanager
async def mail_origin(site: MailSite) -> AsyncIterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(site))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = "127.0.0.1"
    port = server.server_port
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        await asyncio.to_thread(thread.join)
        server.server_close()


__all__ = ["mail_origin"]
