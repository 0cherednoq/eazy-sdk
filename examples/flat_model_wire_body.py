"""Keep public kwargs flat while emitting and capturing a nested JSON document."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar, TypedDict, Unpack, cast

import httpx

from eazy_sdk import AsyncApi, AsyncClient, api
from eazy_sdk.handlers.httpx import AsyncHttpxHandler
from eazy_sdk.request import BodyProjection, JsonBody
from eazy_sdk.response import Json, Responses, Success


class RegisterUser(TypedDict):
    """Public SDK input containing only caller-owned values."""

    login: str
    email: str
    first_name: str
    last_name: str


class AccountWire(TypedDict):
    login: str
    email: str


class ProfileWire(TypedDict):
    first_name: str
    last_name: str


class ClientWire(TypedDict):
    encoding: str
    url: str
    version: str
    platform: str
    timestamp: int


class RegisterUserWire(TypedDict):
    """Private semantic document matching the server contract."""

    account: AccountWire
    profile: ProfileWire
    client: ClientWire


def unix_timestamp() -> int:
    return int(time.time())


@dataclass(frozen=True, slots=True)
class RegisterWireSettings:
    """Constants and a pure per-attempt factory hidden from callers."""

    encoding: str = "utf-8"
    url: str = "https://example.com"
    version: str = "1.0"
    platform: str = "web"
    timestamp_factory: Callable[[], int] = unix_timestamp


@dataclass(frozen=True, slots=True)
class RegisterUserProjection:
    settings: RegisterWireSettings = field(default_factory=RegisterWireSettings)

    def __call__(self, user: RegisterUser) -> RegisterUserWire:
        return {
            "account": {
                "login": user["login"],
                "email": user["email"],
            },
            "profile": {
                "first_name": user["first_name"],
                "last_name": user["last_name"],
            },
            "client": {
                "encoding": self.settings.encoding,
                "url": self.settings.url,
                "version": self.settings.version,
                "platform": self.settings.platform,
                "timestamp": self.settings.timestamp_factory(),
            },
        }


REGISTER_BODY = BodyProjection(
    source=RegisterUser,
    target=RegisterUserWire,
    using=RegisterUserProjection(),
    encoding=JsonBody(),
    name="register-user-v1",
)


@dataclass(frozen=True, slots=True)
class RegisteredUser:
    id: str
    login: str


REGISTER_RESPONSES = Responses[RegisteredUser](
    success=(Success(201, Json(RegisteredUser)),),
)


class RegistrationApi(AsyncApi):
    @api.post(
        "/register",
        operation_id="registerUser",
        body=REGISTER_BODY,
        responses=REGISTER_RESPONSES,
    )
    async def register(self, **request: Unpack[RegisterUser]) -> RegisteredUser:
        raise NotImplementedError


class RegistrationServer(BaseHTTPRequestHandler):
    captured: ClassVar[list[dict[str, object]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = cast(dict[str, object], json.loads(self.rfile.read(length)))
        type(self).captured.append(payload)
        response = json.dumps(
            {"id": "user-42", "login": cast(dict[str, object], payload["account"])["login"]},
            separators=(",", ":"),
        ).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        pass


async def main() -> None:
    RegistrationServer.captured.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), RegistrationServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        raw = httpx.AsyncClient(headers={}, cookies={})
        async with AsyncClient(
            base_url=f"http://127.0.0.1:{server.server_port}",
            handler=AsyncHttpxHandler(raw, owns_client=True),
        ) as client:
            registration = RegistrationApi(client)
            registered = await registration.register(
                login="john",
                email="john@example.com",
                first_name="John",
                last_name="Smith",
            )
        payload = RegistrationServer.captured.pop()
        assert payload["account"] == {
            "login": "john",
            "email": "john@example.com",
        }
        assert payload["profile"] == {
            "first_name": "John",
            "last_name": "Smith",
        }
        client_wire = cast(dict[str, object], payload["client"])
        assert client_wire | {"timestamp": 0} == {
            "encoding": "utf-8",
            "url": "https://example.com",
            "version": "1.0",
            "platform": "web",
            "timestamp": 0,
        }
        assert isinstance(client_wire["timestamp"], int)
        print(f"registered: {registered.login} as {registered.id}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    asyncio.run(main())
