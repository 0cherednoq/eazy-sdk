"""Build and serialize a deeply nested JSON body with an Adaptix converter."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import cast

import httpx
from adaptix import P
from adaptix.conversion import get_converter, link_constant, link_function

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.request import BodyProjection
from eazy_sdk.request.markers import JsonBody


@dataclass(frozen=True, slots=True)
class AccountWire:
    login: str
    email: str


@dataclass(frozen=True, slots=True)
class PersonNameWire:
    first: str
    last: str


@dataclass(frozen=True, slots=True)
class ProfileWire:
    name: PersonNameWire


@dataclass(frozen=True, slots=True)
class RegisterPayloadWire:
    account: AccountWire
    profile: ProfileWire


@dataclass(frozen=True, slots=True)
class RequestMetadataWire:
    locale: str
    encoding: str
    api_version: str
    platform: str
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class RegisterUserWire:
    payload: RegisterPayloadWire
    metadata: RequestMetadataWire


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AdaptixWireDefaults:
    """Wire-only defaults and a replaceable generator for time values."""

    locale: str = "en-US"
    encoding: str = "utf-8"
    api_version: str = "2026-08"
    platform: str = "python"
    now_factory: Callable[[], datetime] = utc_now


type RegisterConverter = Callable[[RegisterUser], RegisterUserWire]


def make_register_converter(
    defaults: AdaptixWireDefaults | None = None,
) -> RegisterConverter:
    """Build one Adaptix projection callable for the nested wire model."""

    defaults = defaults or AdaptixWireDefaults()

    def payload_factory(source: RegisterUser) -> RegisterPayloadWire:
        return RegisterPayloadWire(
            account=AccountWire(
                login=source.login,
                email=source.email,
            ),
            profile=ProfileWire(
                name=PersonNameWire(
                    first=source.first_name,
                    last=source.last_name,
                ),
            ),
        )

    def metadata_factory() -> RequestMetadataWire:
        return RequestMetadataWire(
            locale=defaults.locale,
            encoding=defaults.encoding,
            api_version=defaults.api_version,
            platform=defaults.platform,
            generated_at=defaults.now_factory(),
        )

    converter: RegisterConverter = get_converter(
        RegisterUser,
        RegisterUserWire,
        recipe=[
            link_function(payload_factory, P[RegisterUserWire].payload),
            link_constant(P[RegisterUserWire].metadata, factory=metadata_factory),
        ],
    )
    return converter


@cache
def _register_converter() -> RegisterConverter:
    """Build the converter once, on the first call.

    The converter reads the operation class, and the operation class declares the
    projection, so one of the two has to be resolved lazily.
    """

    return make_register_converter()


def register_to_wire(source: RegisterUser) -> RegisterUserWire:
    return _register_converter()(source)


@dataclass(frozen=True, slots=True)
class RegisteredUser:
    id: str
    login: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUser(HttpOperation[RegisteredUser]):
    """Only values owned by the SDK caller are public operation fields."""

    __http__ = Http.post(
        "/register",
        operation_id="adaptixRegisterUser",
        success={201: RegisteredUser},
        projection=BodyProjection(
            target=RegisterUserWire,
            using=register_to_wire,
            encoding=JsonBody(),
            name="adaptix-register-user-v1",
        ),
    )

    login: str
    email: str
    first_name: str
    last_name: str


class RegistrationApi(SyncApi):
    register = op(RegisterUser)


def registration_service(request: httpx.Request) -> httpx.Response:
    """Verify the actual JSON emitted after projection and model serialization."""

    assert request.method == "POST"
    assert request.url.path == "/register"
    document = cast(dict[str, object], json.loads(request.content))
    assert document["payload"] == {
        "account": {"login": "john", "email": "john@example.com"},
        "profile": {"name": {"first": "John", "last": "Smith"}},
    }

    metadata = cast(dict[str, object], document["metadata"])
    assert metadata | {"generated_at": "<generated>"} == {
        "locale": "en-US",
        "encoding": "utf-8",
        "api_version": "2026-08",
        "platform": "python",
        "generated_at": "<generated>",
    }
    generated_at = datetime.fromisoformat(cast(str, metadata["generated_at"]))
    assert generated_at.tzinfo is not None

    return httpx.Response(201, json={"id": "user-42", "login": "john"})


def main() -> None:
    raw = httpx.Client(
        transport=httpx.MockTransport(registration_service),
        headers={},
        cookies={},
    )
    with Client(
        base_url="https://registration.example",
        handler=HttpxHandler(raw, owns_client=True),
    ) as client:
        registered = RegistrationApi(client).register(
            login="john",
            email="john@example.com",
            first_name="John",
            last_name="Smith",
        )

    print(f"adaptix registered: {registered.login} as {registered.id}")


if __name__ == "__main__":
    main()
