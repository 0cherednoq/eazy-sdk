from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypedDict, Unpack, cast

import msgspec
import pytest
from adaptix.conversion import get_converter
from pydantic import BaseModel

from eazy_sdk import ApiDefaults, SyncApi, api
from eazy_sdk._internal import PlanError
from eazy_sdk.models import default_model_adapters
from eazy_sdk.request import JsonBody
from eazy_sdk.response import Responses
from tests._support.body_projection_proof import BodyProjection
from tests._support.body_projection_proof import JsonBody as ProofJsonBody

ROOT = Path(__file__).parents[2]
RESPONSES: Responses[object] = Responses(success=())


class RegisterUser(TypedDict):
    login: str
    email: str
    first_name: str
    last_name: str


class RegistrationBody(TypedDict):
    user: Annotated[RegisterUser, JsonBody()]


class TypedDictWire(TypedDict):
    value: str


@dataclass
class DataclassWire:
    value: str


class PydanticWire(BaseModel):
    value: str


class MsgspecWire(msgspec.Struct):
    value: str


def _to_wire(source: RegisterUser) -> dict[str, object]:
    return {
        "account": {"login": source["login"], "email": source["email"]},
        "profile": {
            "first_name": source["first_name"],
            "last_name": source["last_name"],
        },
    }


def test_current_unplaced_unpack_failure_is_characterized() -> None:
    with pytest.raises(PlanError, match=r"input field 'login'.*no placement"):

        class RegistrationApi(SyncApi):
            @api.post("/register", operation_id="registerUser", responses=RESPONSES)
            def register(self, **request: Unpack[RegisterUser]) -> object:
                raise NotImplementedError


def test_current_root_body_workaround_exposes_one_wrapper_parameter() -> None:
    class RegistrationApi(SyncApi):
        @api.post("/register", operation_id="registerUserWrapper", responses=RESPONSES)
        def register(self, **request: Unpack[RegistrationBody]) -> object:
            raise NotImplementedError

    descriptor = cast(Any, RegistrationApi.register)
    signature = descriptor.signature

    assert tuple(signature.parameters) == ("self", "request")
    assert "Unpack" in str(signature.parameters["request"].annotation)
    compiled = descriptor.resolve(ApiDefaults()).compile()
    assert tuple(compiled.input_slots) == ("user",)


def test_candidate_derives_a_stable_name_for_plain_and_generated_callables() -> None:
    projection = BodyProjection(
        RegisterUser,
        dict[str, object],
        _to_wire,
        ProofJsonBody(),
    )

    assert projection.fingerprint_name.endswith(":_to_wire")
    assert BodyProjection(
        RegisterUser,
        dict[str, object],
        _to_wire,
        ProofJsonBody(),
        "register-v1",
    ).fingerprint_name == "register-v1"

    first_adaptix = get_converter(RegisterUser, RegisterUser)
    second_adaptix = get_converter(RegisterUser, RegisterUser)
    first_name = BodyProjection(
        RegisterUser,
        RegisterUser,
        first_adaptix,
        ProofJsonBody(),
    ).fingerprint_name
    second_name = BodyProjection(
        RegisterUser,
        RegisterUser,
        second_adaptix,
        ProofJsonBody(),
    ).fingerprint_name
    assert first_name == second_name


@pytest.mark.parametrize(
    ("target", "adapter_name"),
    [
        (TypedDictWire, "typed-dict"),
        (DataclassWire, "dataclass"),
        (PydanticWire, "pydantic"),
        (MsgspecWire, "msgspec"),
    ],
)
def test_target_adapter_matrix_is_fixed(target: type[object], adapter_name: str) -> None:
    assert default_model_adapters().adapter_for_type(target).name == adapter_name


def _run_checker(checker: str, source: Path) -> subprocess.CompletedProcess[str]:
    if checker == "mypy":
        command = [sys.executable, "-m", "mypy", "--strict", str(source)]
    else:
        command = [
            sys.executable,
            "-m",
            "basedpyright",
            "--pythonpath",
            sys.executable,
            "--pythonversion",
            "3.13",
            "--level",
            "error",
            str(source),
        ]
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


POSITIVE_TYPING = r'''# pyright: strict, reportUnknownVariableType=false
from collections.abc import Callable
from typing import TypedDict, Unpack, assert_type

from adaptix import P
from adaptix.conversion import get_converter, link_constant, link_function

from eazy_sdk.request import BodyProjection, JsonBody


class RegisterUser(TypedDict):
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
    timestamp: int


class RegisterUserWire(TypedDict):
    account: AccountWire
    profile: ProfileWire
    client: ClientWire


def account_wire(user: RegisterUser) -> AccountWire:
    return {"login": user["login"], "email": user["email"]}


def profile_wire(user: RegisterUser) -> ProfileWire:
    return {
        "first_name": user["first_name"],
        "last_name": user["last_name"],
    }


def client_wire() -> ClientWire:
    return {"timestamp": 1}


register_to_wire = get_converter(
    RegisterUser,
    RegisterUserWire,
    recipe=(
        link_function(
            account_wire,
            P[RegisterUserWire].account,
        ),
        link_function(
            profile_wire,
            P[RegisterUserWire].profile,
        ),
        link_constant(P[RegisterUserWire].client, factory=client_wire),
    ),
)

projection = BodyProjection(
    RegisterUser,
    RegisterUserWire,
    register_to_wire,
    JsonBody(),
)
assert_type(register_to_wire, Callable[[RegisterUser], RegisterUserWire])
assert_type(projection, BodyProjection[RegisterUser, RegisterUserWire])
assert_type(projection.using, Callable[[RegisterUser], RegisterUserWire])
assert_type(
    projection.using(
        {
            "login": "john",
            "email": "john@example.com",
            "first_name": "John",
            "last_name": "Smith",
        }
    ),
    RegisterUserWire,
)


class UpdateUserBody(TypedDict):
    display_name: str
    timezone: str


class UpdateUserRequest(UpdateUserBody):
    user_id: str


def update_to_wire(source: UpdateUserBody) -> RegisterUserWire:
    return {
        "account": {"login": source["display_name"], "email": ""},
        "profile": {"first_name": source["display_name"], "last_name": ""},
        "client": {"timestamp": 1},
    }


subset_projection = BodyProjection(
    UpdateUserBody,
    RegisterUserWire,
    update_to_wire,
    JsonBody(),
)
assert_type(subset_projection, BodyProjection[UpdateUserBody, RegisterUserWire])


class RegisterCall:
    def __call__(self, **request: Unpack[RegisterUser]) -> RegisterUserWire:
        return projection.using(request)


register = RegisterCall()
assert_type(
    register(
        login="john",
        email="john@example.com",
        first_name="John",
        last_name="Smith",
    ),
    RegisterUserWire,
)


class AsyncRegisterCall:
    async def __call__(self, **request: Unpack[RegisterUser]) -> RegisterUserWire:
        return projection.using(request)


async def call_async(register_async: AsyncRegisterCall) -> None:
    result = await register_async(
        login="john",
        email="john@example.com",
        first_name="John",
        last_name="Smith",
    )
    assert_type(result, RegisterUserWire)
'''


NEGATIVE_TYPING = r'''# pyright: strict
from typing import TypedDict, Unpack

from eazy_sdk.request import BodyProjection, JsonBody


class Source(TypedDict):
    value: str


class Wire(TypedDict):
    nested: str


class RegisterUser(TypedDict):
    login: str
    email: str
    first_name: str
    last_name: str


def wrong_source(source: int) -> Wire:
    return {"nested": str(source)}


def wrong_target(source: Source) -> int:
    return len(source["value"])


bad_source = BodyProjection[Source, Wire](
    Source,
    Wire,
    wrong_source,
    JsonBody(),
)
bad_target = BodyProjection[Source, Wire](
    Source,
    Wire,
    wrong_target,
    JsonBody(),
)


class RegisterCall:
    def __call__(self, **request: Unpack[RegisterUser]) -> Wire:
        return {"nested": request["login"]}


class AsyncRegisterCall:
    async def __call__(self, **request: Unpack[RegisterUser]) -> Wire:
        return {"nested": request["login"]}


def invalid_sync(register: RegisterCall) -> None:
    register(login="john", email="john@example.com", first_name="John")
    register(
        login="john",
        email="john@example.com",
        first_name="John",
        last_name="Smith",
        unknown="value",
    )


async def invalid_async(register: AsyncRegisterCall) -> None:
    await register(login="john", email="john@example.com", first_name="John")
    await register(
        login="john",
        email="john@example.com",
        first_name="John",
        last_name="Smith",
        unknown="value",
    )
'''


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_candidate_constructor_and_adaptix_converter_have_no_any_leakage(
    checker: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="phase21-typing-", dir=ROOT / "tests") as temporary:
        source = Path(temporary) / "positive_body_projection.py"
        source.write_text(POSITIVE_TYPING, encoding="utf-8")

        result = _run_checker(checker, source)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("checker", ["mypy", "basedpyright"])
def test_candidate_rejects_wrong_source_and_target_callables(
    checker: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="phase21-typing-", dir=ROOT / "tests") as temporary:
        source = Path(temporary) / "negative_body_projection.py"
        source.write_text(NEGATIVE_TYPING, encoding="utf-8")

        result = _run_checker(checker, source)

    assert result.returncode == 1
    output = (result.stdout + result.stderr).lower()
    assert output.count("incompatible") >= 2 or output.count("cannot be assigned") >= 2
