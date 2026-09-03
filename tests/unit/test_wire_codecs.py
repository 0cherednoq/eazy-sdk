from __future__ import annotations

import json
from dataclasses import dataclass

from eazy_sdk.codecs import EncodeContext, ScalarEncodeContext
from eazy_sdk.compile import (
    CompiledContract,
    InputField,
    compile_endpoint,
)
from eazy_sdk.core import (
    OperationValues,
    RequestLocation,
)
from eazy_sdk.request import Query
from eazy_sdk.request.prepared import BufferedBody, RequestPreparer


@dataclass(frozen=True)
class Contract:
    operation_id: str
    input_fields: tuple[InputField, ...]
    method: str = "POST"
    path: str = "/items"
    responses: object = None


def prepare(
    contract: Contract, values: dict[str, object]
) -> tuple[CompiledContract[object], BufferedBody, bytes]:
    compiled: CompiledContract[object] = compile_endpoint(contract)
    bound = compiled.bind_input(values)
    request_values = OperationValues.from_bound(compiled.plan.shape, bound)
    request = RequestPreparer("https://example.test").prepare(compiled, request_values)
    assert isinstance(request.body, BufferedBody)
    return compiled, request.body, request.target


@dataclass(frozen=True)
class CanonicalJsonCodec:
    name: str = "canonical-json"
    media_type: str | None = "application/vnd.example+json"

    def encode(self, value: object, context: EncodeContext) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class Payload:
    z: int
    a: str


def test_custom_body_codec_receives_primitives_and_returns_the_exact_wire_bytes() -> None:
    codec = CanonicalJsonCodec()
    contract = Contract(
        "custom-body",
        (InputField("body", "body", Payload, True, RequestLocation.BODY, codec),),
    )
    payload = Payload(2, "one")

    _, body, _ = prepare(contract, {"body": payload})

    assert body.content == b'{"a":"one","z":2}'
    assert body.content_type == b"application/vnd.example+json"
    assert payload == Payload(2, "one")


@dataclass(frozen=True)
class PipeCodec:
    name: str = "pipe"

    def encode(self, value: object, context: ScalarEncodeContext) -> str:
        assert context.location == "query"
        assert context.operation_id == "custom-query"
        assert isinstance(value, list)
        return "|".join(str(item) for item in value)


def test_custom_scalar_codec_collapses_a_collection_into_one_query_pair() -> None:
    query = Query("tag", codec=PipeCodec())
    contract = Contract(
        "custom-query",
        (InputField("tags", "tag", list[str], True, RequestLocation.QUERY, query),),
        method="GET",
    )

    _, _, target = prepare(contract, {"tags": ["a", "b"]})

    assert target == b"/items?tag=a%7Cb"
