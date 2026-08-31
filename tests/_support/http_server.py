"""Deterministic localhost HTTP server for cross-component tests."""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self, cast
from urllib.parse import parse_qs, parse_qsl, urlsplit


@dataclass(frozen=True, slots=True)
class CapturedExchange:
    method: str
    target: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.exchanges: list[CapturedExchange] = []
        self.exchanges_lock = threading.Lock()


class LocalHttpServer:
    """A small stateful HTTP/1.1 origin that never leaves localhost."""

    def __init__(self) -> None:
        self._server = _Server()
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = cast(tuple[str, int], self._server.server_address)
        return f"http://{host}:{port}"

    @property
    def exchanges(self) -> tuple[CapturedExchange, ...]:
        with self._server.exchanges_lock:
            return tuple(self._server.exchanges)

    def __enter__(self) -> Self:
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("localhost HTTP test server did not stop")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Server

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        with self.server.exchanges_lock:
            self.server.exchanges.append(
                CapturedExchange(self.command, self.path, tuple(self.headers.items()), body)
            )
        split = urlsplit(self.path)
        if split.path == "/echo":
            self._json(
                {
                    "method": self.command,
                    "query": parse_qsl(split.query, keep_blank_values=True),
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "body": body.decode("utf-8", errors="replace"),
                    "body_base64": base64.b64encode(body).decode("ascii"),
                }
            )
            return
        if split.path.startswith("/status/"):
            status = int(split.path.rsplit("/", 1)[1])
            self._send(status, b"")
            return
        if split.path == "/headers":
            self._send(
                200,
                b"headers",
                (("X-Test", "value"), ("X-Duplicate", "a"), ("X-Duplicate", "b")),
            )
            return
        if split.path == "/cookies/set":
            self._send(
                200,
                b"cookies",
                (("Set-Cookie", "first=one; Path=/"), ("Set-Cookie", "second=two; Path=/")),
            )
            return
        if split.path == "/cookies/show":
            self._json({"cookie": self.headers.get("Cookie", "")})
            return
        if split.path.startswith("/redirect/"):
            status = int(split.path.rsplit("/", 1)[1])
            target = parse_qs(split.query).get("to", ["/echo"])[0]
            self._send(status, b"", (("Location", target),))
            return
        if split.path == "/stream":
            content = b"first-second-third"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                for chunk in (b"first-", b"second-", b"third"):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            return
        if split.path == "/delay":
            delay = float(parse_qs(split.query).get("seconds", ["0.2"])[0])
            time.sleep(min(delay, 1.0))
            self._send(200, b"delayed")
            return
        if split.path == "/disconnect":
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if split.path == "/auth":
            value = self.headers.get("Authorization")
            self._json({"authorized": value == "Bearer test-token", "authorization": value})
            return
        self._send(404, b"not found")

    def _json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, (("Content-Type", "application/json; charset=utf-8"),))

    def _send(
        self,
        status: int,
        body: bytes,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and body:
            try:
                self.wfile.write(body)
            except ConnectionError, OSError:
                # Timeout/disconnect tests deliberately close the client side first.
                self.close_connection = True


__all__ = ["CapturedExchange", "LocalHttpServer"]
