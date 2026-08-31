"""A local HTTP/1.1 first-hop byte capture server used by transport tests."""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from importlib.metadata import version
from typing import Self


@dataclass(frozen=True, slots=True)
class CapturedRequest:
    request_line: bytes
    header_lines: tuple[bytes, ...]
    body: bytes
    transport: str
    transport_version: str


class RawCaptureServer:
    """Capture exactly one HTTP/1.1 request without framework normalization."""

    def __init__(self, *, status: int = 200, headers: tuple[tuple[bytes, bytes], ...] = ()) -> None:
        self._status = status
        self._response_headers = headers
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._thread: threading.Thread | None = None
        self.capture: CapturedRequest | None = None
        self.transport = "unknown"

    @property
    def url(self) -> str:
        host, port = self._listener.getsockname()
        return f"http://{host}:{port}"

    def identify(self, distribution: str) -> None:
        self.transport = distribution

    def __enter__(self) -> Self:
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _serve(self) -> None:
        connection, _ = self._listener.accept()
        with connection:
            head, rest = _read_head(connection)
            lines = head.split(b"\r\n")
            length = _content_length(lines[1:])
            body = rest
            while len(body) < length:
                body += connection.recv(length - len(body))
            try:
                transport_version = version(self.transport)
            except Exception:
                transport_version = "unknown"
            self.capture = CapturedRequest(
                request_line=lines[0],
                header_lines=tuple(lines[1:]),
                body=body[:length],
                transport=self.transport,
                transport_version=transport_version,
            )
            reason = b"OK" if self._status == 200 else b"Found"
            response_headers = b"".join(
                name + b": " + value + b"\r\n" for name, value in self._response_headers
            )
            connection.sendall(
                b"HTTP/1.1 "
                + str(self._status).encode("ascii")
                + b" "
                + reason
                + b"\r\n"
                + response_headers
                + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )


def _read_head(connection: socket.socket) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(65536)
        if not chunk:
            raise RuntimeError("connection closed before request headers")
        data += chunk
    head, body = data.split(b"\r\n\r\n", 1)
    return head, body


def _content_length(lines: list[bytes]) -> int:
    for line in lines:
        name, separator, value = line.partition(b":")
        if separator and name.lower() == b"content-length":
            return int(value.strip())
    return 0
