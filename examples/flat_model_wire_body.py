"""Keep the public operation flat while the wire carries a nested, service-owned document.

The caller passes four values it actually owns. Everything else in the body — the nesting,
the constants, the timestamp and the device the SDK runs on — belongs to the service
contract, so it is built by a projection instead of being asked for at every call site.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypedDict, cast

import httpx

from eazy_sdk import Client, Http, HttpOperation, Identity, SyncApi, op
from eazy_sdk.dependencies import (
    DependencyContext,
    DependencyRegistry,
    Injected,
    dependency,
)
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import Body, BodyProjection

# --- what the SDK user configures once, not per call ------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceContext:
    """Values the application knows about itself and the SDK sends on its behalf."""

    device_id: str
    device_type: str
    app_version: str


DEVICE = dependency(DeviceContext, name="device")


@dataclass(frozen=True, slots=True)
class StaticDevice:
    """The provider an application registers: one device for the whole SDK lifetime."""

    device: DeviceContext

    def resolve(self, context: DependencyContext) -> DeviceContext:
        return self.device


# --- the private wire document ----------------------------------------------------------


class AccountWire(TypedDict):
    login: str
    email: str


class ProfileWire(TypedDict):
    first_name: str
    last_name: str


class DeviceWire(TypedDict):
    id: str
    type: str
    app_version: str


class ClientWire(TypedDict):
    encoding: str
    url: str
    version: str
    platform: str
    timestamp: int
    device: DeviceWire


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
    """Build the wire document from the operation value and the resolved device."""

    settings: RegisterWireSettings = field(default_factory=RegisterWireSettings)

    def __call__(self, user: RegisterUser, injected: Injected) -> RegisterUserWire:
        device = cast(DeviceContext, injected[DEVICE.dependency])
        return {
            "account": {
                "login": user.login,
                "email": user.email,
            },
            "profile": {
                "first_name": user.first_name,
                "last_name": user.last_name,
            },
            "client": {
                "encoding": self.settings.encoding,
                "url": self.settings.url,
                "version": self.settings.version,
                "platform": self.settings.platform,
                "timestamp": self.settings.timestamp_factory(),
                "device": {
                    "id": device.device_id,
                    "type": device.device_type,
                    "app_version": device.app_version,
                },
            },
        }


@dataclass(frozen=True, slots=True)
class RegisteredUser:
    id: str
    login: str


# --- the operation the caller sees ------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUser(HttpOperation[RegisteredUser]):
    """Public SDK input containing only caller-owned values."""

    __http__ = Http.post(
        "/register",
        operation_id="registerUser",
        success={201: RegisteredUser},
        requires=(DEVICE,),
        projection=BodyProjection(
            target=RegisterUserWire,
            using=RegisterUserProjection(),
            encoding=Body.json(),
            name="register-user-v1",
        ),
    )

    login: str
    email: str
    first_name: str
    last_name: str


class RegistrationApi(SyncApi):
    register = op(RegisterUser)


def registration_service(request: httpx.Request) -> httpx.Response:
    """Verify the document the server actually receives."""

    assert request.method == "POST"
    assert request.url.path == "/register"
    payload = cast(dict[str, object], json.loads(request.content))
    assert payload["account"] == {"login": "john", "email": "john@example.com"}
    assert payload["profile"] == {"first_name": "John", "last_name": "Smith"}

    client_wire = cast(dict[str, object], payload["client"])
    assert client_wire | {"timestamp": 0} == {
        "encoding": "utf-8",
        "url": "https://example.com",
        "version": "1.0",
        "platform": "web",
        "timestamp": 0,
        "device": {
            "id": "device-7",
            "type": "desktop",
            "app_version": "3.4.1",
        },
    }
    assert isinstance(client_wire["timestamp"], int)

    return httpx.Response(201, json={"id": "user-42", "login": "john"})


def main() -> None:
    dependencies = DependencyRegistry()
    dependencies.register(
        DEVICE.dependency,
        StaticDevice(
            DeviceContext(device_id="device-7", device_type="desktop", app_version="3.4.1")
        ),
    )

    raw = httpx.Client(
        transport=httpx.MockTransport(registration_service),
        headers={},
        cookies={},
    )
    with Client(
        base_url="https://registration.example",
        handler=HttpxHandler(raw, owns_client=True),
    ) as client:
        registration = RegistrationApi(client, identity=Identity(dependencies=dependencies))
        registered = registration.register(
            login="john",
            email="john@example.com",
            first_name="John",
            last_name="Smith",
        )

    print(f"registered: {registered.login} as {registered.id}")


if __name__ == "__main__":
    main()
