"""The retort an SDK already keeps is enough to serialize its requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import pytest
from adaptix import Retort, name_mapping
from eazy_sdk_adaptix import AdaptixModelAdapter, adaptix_models

from eazy_sdk import Client, Http, HttpOperation, SyncApi, op
from eazy_sdk.handlers.httpx import HttpxHandler
from eazy_sdk.models import default_model_adapters
from eazy_sdk.request import markers
from eazy_sdk.serialization import Serialization


@dataclass(frozen=True, slots=True)
class RegisterUser:
    full_name: str
    email_address: str


@dataclass(frozen=True, slots=True)
class Registered:
    id: str


RETORT = Retort(
    recipe=[
        name_mapping(RegisterUser, map={"full_name": "fullName", "email_address": "email"}),
    ]
)

NAMES: Mapping[type[object], Mapping[str, str]] = {
    RegisterUser: {"full_name": "fullName", "email_address": "email"}
}


def _models() -> Any:
    return adaptix_models(
        default_model_adapters(), retort=RETORT, types=(RegisterUser,), names=NAMES
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Register(HttpOperation[Registered]):
    __http__ = Http.post("/users", operation_id="register")

    body: Annotated[RegisterUser, markers.JsonBody()]


class UsersApi(SyncApi):
    register = op(Register)


@dataclass(frozen=True, slots=True, kw_only=True)
class Untouched:
    """A plain dataclass the retort was never told about."""

    value: int = 1


def test_types_are_required() -> None:
    with pytest.raises(ValueError) as failure:
        AdaptixModelAdapter(retort=RETORT, types=())
    assert "would only stand in front of the built-in one" in str(failure.value)


def test_retort_name_mapping_reaches_the_wire() -> None:
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json={"id": "user-42"})

    raw = httpx.Client(transport=httpx.MockTransport(handler))
    with Client(
        base_url="https://api.example", handler=HttpxHandler(raw, owns_client=True)
    ) as client:
        sdk = UsersApi(client, serialization=Serialization(models=_models()))
        assert sdk.register(body=RegisterUser("Ada", "ada@example.com")).id == "user-42"

    assert sent == [b'{"fullName":"Ada","email":"ada@example.com"}']


def test_registry_stays_unambiguous_beside_the_builtin_adapter() -> None:
    models = _models()
    assert models.adapter_for_type(RegisterUser).name == "adaptix"
    assert models.adapter_for_type(Untouched).name == "adaptix", "it stands in for dataclass"
    assert models.adapter_for_value(RegisterUser("Ada", "a@b.c")).name == "adaptix"
    assert not models.adapter_for_type(Untouched).serves(Untouched)
    assert models.fields(Untouched)[0].wire_name == "value"


def test_fields_read_the_declared_wire_names() -> None:
    models = _models()
    fields = models.fields(RegisterUser)
    assert [(item.name, item.wire_name, item.required) for item in fields] == [
        ("full_name", "fullName", True),
        ("email_address", "email", True),
    ]


def test_load_and_evolve_go_through_the_retort_and_the_library() -> None:
    models = _models()
    loaded = models.load(RegisterUser, {"fullName": "Ada", "email": "ada@example.com"})
    assert loaded == RegisterUser("Ada", "ada@example.com")
    assert models.evolve(loaded, full_name="Grace").full_name == "Grace"
    assert models.adapter_for_type(RegisterUser).frozen(RegisterUser) is True
