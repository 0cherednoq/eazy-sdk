from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Annotated, Any, NotRequired, TypedDict

import msgspec
import pytest
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from eazy_sdk._internal import (
    CompiledContract,
    InputField,
    OperationValues,
    RequestLocation,
    compile_endpoint,
)
from eazy_sdk._internal.http_plan import ExecutionPlan
from eazy_sdk.ext import BufferedBody, RequestPreparer
from eazy_sdk.models import (
    AmbiguousModelAdapterError,
    DataclassModelAdapter,
    ModelAdapterRegistry,
    ModelDumpMode,
    ModelField,
    default_model_adapters,
)
from eazy_sdk.request import FormBody, JsonBody, MultipartBody
from eazy_sdk.response import (
    Extracted,
    Json,
    NormalizedResponse,
    ResponseContext,
    Responses,
    Success,
)
from eazy_sdk.response.cases import ParseAttempt, ParsedValue, SuccessOutcome


@dataclass
class DataclassAddress:
    city: str


@dataclass
class DataclassUser:
    name: str
    age: int
    address: DataclassAddress
    enabled: bool = True
    note: str | None = None


class PydanticAddress(BaseModel):
    city: str


class PydanticUser(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    name: str = Field(serialization_alias="displayName")
    age: int
    address: PydanticAddress
    enabled: bool = True
    note: str | None = None


class MsgspecAddress(msgspec.Struct):
    city: str


class MsgspecUser(msgspec.Struct, rename={"name": "displayName"}):
    name: str
    age: int
    address: MsgspecAddress
    enabled: bool = True
    note: str | None = None


class FlatUserRequest(TypedDict):
    name: str
    age: int
    note: NotRequired[str]


@dataclass(frozen=True)
class RequestContract:
    annotation: type[object]
    descriptor: JsonBody | FormBody | MultipartBody = dataclass_field(default_factory=JsonBody)
    operation_id: str = "model-request"
    method: str = "POST"
    path: str = "/users"
    responses: object = None

    @property
    def input_fields(self) -> tuple[InputField, ...]:
        return (
            InputField(
                "body",
                "body",
                self.annotation,
                True,
                RequestLocation.BODY,
                self.descriptor,
            ),
        )


@pytest.mark.parametrize(
    ("model", "payload", "adapter_name"),
    [
        (
            DataclassUser,
            {"name": "Ada", "age": "37", "address": {"city": "London"}},
            "dataclass",
        ),
        (
            PydanticUser,
            {"name": "Ada", "age": "37", "address": {"city": "London"}},
            "pydantic",
        ),
        (
            MsgspecUser,
            {"displayName": "Ada", "age": "37", "address": {"city": "London"}},
            "msgspec",
        ),
    ],
)
def test_model_adapters_load_nested_models_and_preserve_declared_field_order(
    model: type[Any], payload: dict[str, object], adapter_name: str
) -> None:
    registry = default_model_adapters()

    value = registry.load(model, payload)
    fields = registry.fields(model)

    assert value.name == "Ada"
    assert value.age == 37
    assert value.address.city == "London"
    assert registry.adapter_for_type(model).name == adapter_name
    assert [field.name for field in fields] == ["name", "age", "address", "enabled", "note"]


@pytest.mark.parametrize(
    "value",
    [
        DataclassUser("Ada", 37, DataclassAddress("London"), note=None),
        PydanticUser(name="Ada", age=37, address=PydanticAddress(city="London")),
        MsgspecUser("Ada", 37, MsgspecAddress("London")),
    ],
)
def test_model_adapters_dump_without_mutation_and_preserve_model_defaults(value: object) -> None:
    registry = default_model_adapters()
    before = repr(value)

    dumped = registry.dump(value)

    assert dumped == {
        "displayName" if isinstance(value, PydanticUser | MsgspecUser) else "name": "Ada",
        "age": 37,
        "address": {"city": "London"},
        "enabled": True,
        "note": None,
    }
    assert repr(value) == before


def test_typed_dict_adapter_validates_required_optional_unknown_and_value_types() -> None:
    registry = default_model_adapters()

    assert registry.load(FlatUserRequest, {"name": "Ada", "age": 37}) == {
        "name": "Ada",
        "age": 37,
    }
    assert [field.name for field in registry.fields(FlatUserRequest)] == [
        "name",
        "age",
        "note",
    ]

    with pytest.raises(TypeError, match=r"missing required field FlatUserRequest\.age"):
        registry.load(FlatUserRequest, {"name": "Ada"})
    with pytest.raises(TypeError, match=r"unknown fields for FlatUserRequest: \['extra'\]"):
        registry.load(FlatUserRequest, {"name": "Ada", "age": 37, "extra": True})
    assert registry.load(FlatUserRequest, {"name": "Ada", "age": "37"})["age"] == 37


class PydanticOwnedPolicy(BaseModel):
    name: str
    enabled: bool = True
    note: str | None = Field(default=None, exclude_if=lambda value: value is None)


class PydanticOwnedSerializer(BaseModel):
    amount: int

    @field_serializer("amount")
    def serialize_amount(self, value: int) -> str:
        return f"USD:{value}"


class PydanticMultipart(BaseModel):
    document: bytes
    note: str | None = Field(default=None, exclude_if=lambda value: value is None)


class MsgspecOwnedPolicy(msgspec.Struct, omit_defaults=True):
    name: str
    enabled: bool = True
    note: str | None = None


def test_pydantic_model_owns_field_exclusion_and_unset_defaults() -> None:
    dumped = default_model_adapters().dump(PydanticOwnedPolicy(name="Ada"))

    assert dumped == {"name": "Ada", "enabled": True}


class PydanticLocalNames(BaseModel):
    name: str = Field(serialization_alias="displayName")


def test_pydantic_model_owns_alias_serialization() -> None:
    registry = default_model_adapters()

    assert registry.dump(PydanticLocalNames(name="Ada")) == {"name": "Ada"}
    assert registry.fields(PydanticLocalNames)[0].wire_name == "name"
    assert registry.fields(PydanticUser)[0].wire_name == "displayName"


def test_pydantic_model_owns_custom_field_serializers() -> None:
    assert default_model_adapters().dump(PydanticOwnedSerializer(amount=7)) == {"amount": "USD:7"}


def test_msgspec_model_owns_omit_defaults_policy() -> None:
    dumped = default_model_adapters().dump(MsgspecOwnedPolicy("Ada"))

    assert dumped == {"name": "Ada"}


@pytest.mark.parametrize(
    ("value", "descriptor", "expected"),
    [
        (
            PydanticOwnedPolicy(name="Ada"),
            FormBody(),
            b"name=Ada&enabled=true",
        ),
        (
            MsgspecOwnedPolicy("Ada"),
            FormBody(),
            b"name=Ada",
        ),
    ],
)
def test_form_body_uses_native_model_serialization(
    value: object, descriptor: FormBody, expected: bytes
) -> None:
    compiled: CompiledContract[object] = compile_endpoint(RequestContract(type(value), descriptor))
    bound = compiled.bind_input({"body": value})
    values = OperationValues.from_bound(compiled.plan.shape, bound)

    prepared = RequestPreparer("https://example.test").prepare(compiled, values)

    assert isinstance(prepared.body, BufferedBody)
    assert prepared.body.content == expected


def test_multipart_body_uses_native_msgspec_omit_defaults() -> None:
    value = MsgspecOwnedPolicy("Ada")
    compiled: CompiledContract[object] = compile_endpoint(
        RequestContract(type(value), MultipartBody(boundary="native-policy"))
    )
    bound = compiled.bind_input({"body": value})
    values = OperationValues.from_bound(compiled.plan.shape, bound)

    prepared = RequestPreparer("https://example.test").prepare(compiled, values)

    assert isinstance(prepared.body, BufferedBody)
    assert prepared.body.content == (
        b'--native-policy\r\nContent-Disposition: form-data; name="name"\r\n\r\n'
        b"Ada\r\n--native-policy--\r\n"
    )


def test_multipart_uses_python_mode_and_preserves_non_utf8_pydantic_bytes() -> None:
    value = PydanticMultipart(document=b"\x00\xff")
    compiled: CompiledContract[object] = compile_endpoint(
        RequestContract(type(value), MultipartBody(boundary="binary-policy"))
    )
    bound = compiled.bind_input({"body": value})
    values = OperationValues.from_bound(compiled.plan.shape, bound)

    prepared = RequestPreparer("https://example.test").prepare(compiled, values)

    assert isinstance(prepared.body, BufferedBody)
    assert prepared.body.content == (
        b'--binary-policy\r\nContent-Disposition: form-data; name="document"\r\n\r\n'
        b"\x00\xff\r\n--binary-policy--\r\n"
    )


def test_json_body_has_no_model_dump_policy() -> None:
    with pytest.raises(TypeError, match="exclude_none"):
        JsonBody(exclude_none=True)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("value", "wire_name"),
    [
        (DataclassUser("Ada", 37, DataclassAddress("London")), "name"),
        (PydanticUser(name="Ada", age=37, address=PydanticAddress(city="London")), "displayName"),
        (MsgspecUser("Ada", 37, MsgspecAddress("London")), "displayName"),
    ],
)
def test_json_request_body_uses_the_same_model_adapter_matrix(
    value: object, wire_name: str
) -> None:
    compiled: CompiledContract[object] = compile_endpoint(RequestContract(type(value)))
    bound = compiled.bind_input({"body": value})
    values = OperationValues.from_bound(compiled.plan.shape, bound)

    prepared = RequestPreparer("https://example.test").prepare(compiled, values)

    assert isinstance(prepared.body, BufferedBody)
    primitive = json.loads(prepared.body.content)
    assert list(primitive) == [wire_name, "age", "address", "enabled", "note"]
    assert primitive[wire_name] == "Ada"
    assert primitive["address"] == {"city": "London"}
    assert primitive["enabled"] is True
    assert primitive["note"] is None


@pytest.mark.parametrize(
    "model",
    [DataclassUser, PydanticUser, MsgspecUser],
)
def test_json_response_cases_use_the_model_adapter_registry(model: type[Any]) -> None:
    name = "displayName" if model is MsgspecUser else "name"
    response = NormalizedResponse(
        200,
        "https://example.test/user",
        "GET",
        [("Content-Type", "application/json")],
        f'{{"{name}":"Ada","age":"37","address":{{"city":"London"}}}}'.encode(),
    )
    outcome: Any = Responses(success=(Success(200, Json(model)),)).inspect(
        ResponseContext(response)
    )

    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value.name == "Ada"
    assert outcome.value.age == 37


class MetadataMarker:
    pass


@dataclass
class MarkedDataclass:
    value: Annotated[str, MetadataMarker()]


class MarkedPydantic(BaseModel):
    value: Annotated[str, MetadataMarker()]


class MarkedMsgspec(msgspec.Struct):
    value: Annotated[str, MetadataMarker()]


@pytest.mark.parametrize("model", [MarkedDataclass, MarkedPydantic, MarkedMsgspec])
def test_model_field_metadata_is_available_without_a_framework_base_class(
    model: type[object],
) -> None:
    metadata = default_model_adapters().fields(model)[0].metadata
    assert len([item for item in metadata if isinstance(item, MetadataMarker)]) == 1


class CustomModel:
    def __init__(self, value: str) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CustomModel) and self.value == other.value


@dataclass(frozen=True)
class ClaimingAdapter:
    name: str

    def supports_type(self, annotation: object) -> bool:
        return annotation is CustomModel

    def supports_value(self, value: object) -> bool:
        return isinstance(value, CustomModel)

    def fields(self, annotation: object) -> tuple[ModelField, ...]:
        return ()

    def dump(
        self,
        value: object,
        *,
        mode: ModelDumpMode,
        registry: ModelAdapterRegistry,
    ) -> object:
        assert isinstance(value, CustomModel)
        return {"value": value.value}

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        registry: ModelAdapterRegistry,
    ) -> T:
        assert isinstance(value, dict)
        return annotation(**value)


def test_ambiguous_custom_model_adapters_fail_deterministically() -> None:
    registry = ModelAdapterRegistry(
        (
            ClaimingAdapter("first"),
            ClaimingAdapter("second"),
        )
    )
    with pytest.raises(AmbiguousModelAdapterError, match="first, second"):
        registry.adapter_for_type(CustomModel)


def test_registry_replaces_builtin_adapter_without_creating_ambiguity() -> None:
    replacement = DataclassModelAdapter()
    registry = default_model_adapters().replace_adapter("dataclass", replacement)

    assert registry.adapter_for_type(DataclassUser) is replacement
    with pytest.raises(ValueError, match="must keep name"):
        registry.replace_adapter("dataclass", ClaimingAdapter("custom"))
    with pytest.raises(ValueError, match="unknown model adapter"):
        registry.replace_adapter("unknown", ClaimingAdapter("unknown"))


def test_model_adapter_name_version_and_implementation_affect_plan_fingerprint() -> None:
    first = ModelAdapterRegistry((ClaimingAdapter("first"),))
    second = ModelAdapterRegistry((ClaimingAdapter("second"),))
    contract = RequestContract(CustomModel)

    first_plan: ExecutionPlan[object] = compile_endpoint(
        contract, fingerprint_context=first.fingerprint_components()
    ).plan
    second_plan: ExecutionPlan[object] = compile_endpoint(
        contract, fingerprint_context=second.fingerprint_components()
    ).plan

    assert first_plan.fingerprint != second_plan.fingerprint
    assert first.fingerprint_components()[0].startswith("model-adapter:first:")


def test_custom_model_adapter_is_used_explicitly_by_response_parsing() -> None:
    registry = default_model_adapters().with_adapter(ClaimingAdapter("custom"))
    response = NormalizedResponse(
        200,
        "https://example.test/custom",
        "GET",
        [("Content-Type", "application/json")],
        b'{"value":"from-wire"}',
    )

    outcome: Any = Responses(success=(Success(200, Json(CustomModel)),)).inspect(
        ResponseContext(response, models=registry)
    )

    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value == CustomModel("from-wire")
    assert registry.dump(CustomModel("to-wire")) == {"value": "to-wire"}


class PrimitiveExtractor:
    name = "primitive-fixture"

    def __init__(self) -> None:
        self.binds = 0
        self.extracts = 0

    def bind(self, response: ResponseContext[object]) -> PrimitiveExtractor:
        self.binds += 1
        return self

    def extract(self, model: type[object]) -> ParseAttempt[object]:
        self.extracts += 1
        return ParsedValue({"value": "from-extractor"})


def test_custom_response_extractor_returns_primitives_then_model_adapter_loads() -> None:
    extractor = PrimitiveExtractor()
    registry = default_model_adapters().with_adapter(ClaimingAdapter("custom"))
    response = NormalizedResponse(200, "https://example.test", "GET", [], b"ignored")

    outcome: Any = Responses(
        success=(Success(200, Extracted(CustomModel, using=extractor)),)
    ).inspect(ResponseContext(response, models=registry))

    assert isinstance(outcome, SuccessOutcome)
    assert outcome.value == CustomModel("from-extractor")
    assert extractor.binds == 1
    assert extractor.extracts == 1
