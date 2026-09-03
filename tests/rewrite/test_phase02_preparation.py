from __future__ import annotations

import dataclasses
import gzip
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from io import BytesIO
from typing import Any, cast

import pytest

from eazy_sdk.compile import (
    CompiledContract,
    InputField,
    compile_endpoint,
)
from eazy_sdk.core import (
    OperationValues,
    RequestLocation,
)
from eazy_sdk.core.errors import BindingError, PlanError
from eazy_sdk.models import (
    ModelAdapterRegistry,
    ModelDumpMode,
    ModelField,
    default_model_adapters,
)
from eazy_sdk.request import (
    BytesBody,
    Cookie,
    FormBody,
    Header,
    JsonBody,
    MultipartBody,
    MultipartPart,
    Path,
    Query,
    ReplayableStreamBody,
    WireOptions,
)
from eazy_sdk.request.prepared import (
    BufferedBody,
    PreparedRequest,
    ReplayableBodyStream,
    RequestPreparer,
    UnsignedPreparedRequest,
)


@dataclass(frozen=True)
class Contract:
    operation_id: str = "operation"
    method: str = "POST"
    path: str = "/payments/{payment_id}"
    parameters: tuple[object, ...] = (
        Path("payment_id"),
        Query("tag", explode=False),
        Query("empty"),
        Header("X-Trace"),
        Cookie("session"),
    )
    body: object | None = None
    responses: object = "responses"
    wire: WireOptions | None = None
    input_fields: tuple[InputField, ...] = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        fields: list[InputField] = []
        for parameter in self.parameters:
            name = cast(str, cast(Any, parameter).name)
            location = {
                Path: RequestLocation.PATH,
                Query: RequestLocation.QUERY,
                Header: RequestLocation.HEADER,
                Cookie: RequestLocation.COOKIE,
            }[type(parameter)]
            fields.append(
                InputField(
                    name,
                    name,
                    object,
                    isinstance(parameter, Path),
                    location,
                    cast(Any, parameter),
                )
            )
        if self.body is not None:
            fields.append(
                InputField(
                    "body",
                    "body",
                    object,
                    True,
                    RequestLocation.BODY,
                    cast(Any, self.body),
                )
            )
        object.__setattr__(self, "input_fields", tuple(fields))


def prepare(
    contract: Contract,
    *,
    path: dict[str, object] | None = None,
    query: dict[str, object] | None = None,
    headers: dict[str, object] | None = None,
    cookies: dict[str, object] | None = None,
    body: object = None,
    has_body: bool = False,
    models: ModelAdapterRegistry | None = None,
) -> UnsignedPreparedRequest:
    compiled: CompiledContract[object] = compile_endpoint(contract)
    request = {**(path or {}), **(query or {}), **(headers or {}), **(cookies or {})}
    if has_body:
        request["body"] = body
    arguments = compiled.bind_input(request)
    values = OperationValues.from_bound(compiled.plan.shape, arguments)
    return RequestPreparer(
        "https://api.example",
        models=models or default_model_adapters(),
    ).prepare(compiled, values)


def test_query_headers_cookies_and_automatic_fields_are_exact_and_ordered() -> None:
    request = prepare(
        Contract(method="GET"),
        path={"payment_id": "pay /42"},
        query={"tag": ["a b", "~/%"], "empty": ""},
        headers={"X-Trace": ["one", "two"]},
        cookies={"session": "secret"},
    )
    assert request.target == b"/payments/pay%20%2F42?tag=a%20b%2C~%2F%25&empty="
    assert [(item.name, item.value) for item in request.headers] == [
        (b"X-Trace", b"one"),
        (b"X-Trace", b"two"),
        (b"Host", b"api.example"),
        (b"Content-Length", b"0"),
        (b"Cookie", b"session=secret"),
    ]
    assert request.view is not None
    assert [pair.value for pair in request.view.query_pairs] == [b"a b,~/%", b""]


class Kind(Enum):
    FAST = "fast"


@dataclass
class Nested:
    z: int
    a: str


@dataclass
class JsonModel:
    amount: Decimal
    created: datetime
    kind: Kind
    nested: Nested
    optional: str | None = None


def test_json_uses_recursive_declaration_order_and_exact_utf8_bytes() -> None:
    contract = Contract(
        body=JsonBody(),
        parameters=(Path("payment_id"),),
    )
    model = JsonModel(
        Decimal("10.50"),
        datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
        Kind.FAST,
        Nested(2, "Привет"),
    )
    request = prepare(contract, path={"payment_id": "42"}, body=model, has_body=True)
    assert isinstance(request.body, BufferedBody)
    assert request.body.content == (
        b'{"amount":"10.50","created":"2026-08-14T12:00:00+00:00",'
        b'"kind":"fast","nested":{"z":2,"a":"\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"},'
        b'"optional":null}'
    )
    assert (b"Content-Type", b"application/json") in [
        (item.name, item.value) for item in request.headers
    ]
    assert (b"Content-Length", str(len(request.body.content)).encode()) in [
        (item.name, item.value) for item in request.headers
    ]


def test_explicit_body_order_changes_only_that_operation() -> None:
    body = {"amount": 1, "currency": "USD", "metadata": {"z": 1, "a": 2}}
    default = prepare(
        Contract(
            parameters=(Path("payment_id"),),
            body=JsonBody(),
        ),
        path={"payment_id": "1"},
        body=body,
        has_body=True,
    )
    ordered = prepare(
        Contract(
            parameters=(Path("payment_id"),),
            body=JsonBody(),
            wire=WireOptions(body_order=("currency", "amount")),
        ),
        path={"payment_id": "1"},
        body=body,
        has_body=True,
    )
    assert isinstance(default.body, BufferedBody) and isinstance(ordered.body, BufferedBody)
    assert default.body.content.startswith(b'{"amount"')
    assert ordered.body.content.startswith(b'{"currency":"USD","amount":1')
    with pytest.raises(PlanError, match="unknown body order"):
        prepare(
            Contract(
                parameters=(Path("payment_id"),),
                body=JsonBody(),
                wire=WireOptions(body_order=("missing",)),
            ),
            path={"payment_id": "1"},
            body=body,
            has_body=True,
        )


@pytest.mark.parametrize(
    ("descriptor", "value", "expected"),
    [
        (FormBody(), {"tag": "a b,c", "empty": ""}, b"tag=a+b%2Cc&empty="),
        (BytesBody(), b"raw\x00bytes", b"raw\x00bytes"),
        (
            BytesBody(content_encoding="gzip"),
            b"compress me",
            gzip.compress(b"compress me", mtime=0),
        ),
    ],
)
def test_form_raw_and_compressed_golden_bytes(
    descriptor: object, value: object, expected: bytes
) -> None:
    request = prepare(
        Contract(parameters=(Path("payment_id"),), body=descriptor),
        path={"payment_id": "1"},
        body=value,
        has_body=True,
    )
    assert isinstance(request.body, BufferedBody)
    assert request.body.content == expected


def test_multipart_boundary_parts_headers_and_binary_content_are_owned_by_preparer() -> None:
    request = prepare(
        Contract(
            parameters=(Path("payment_id"),),
            body=MultipartBody(boundary="golden-boundary"),
        ),
        path={"payment_id": "1"},
        body={
            "title": "café",
            "file": MultipartPart(
                b"\x00\xff", filename="report.bin", content_type="application/octet-stream"
            ),
        },
        has_body=True,
    )
    assert isinstance(request.body, BufferedBody)
    assert request.body.content == (
        b'--golden-boundary\r\nContent-Disposition: form-data; name="title"\r\n\r\n'
        b'caf\xc3\xa9\r\n--golden-boundary\r\nContent-Disposition: form-data; name="file"; '
        b'filename="report.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
        b"\x00\xff\r\n--golden-boundary--\r\n"
    )


class CountingModel:
    def __init__(self) -> None:
        self.calls = 0


@dataclass(frozen=True)
class CountingModelAdapter:
    name: str = "counting"

    def supports_type(self, annotation: object) -> bool:
        return annotation is CountingModel

    def supports_value(self, value: object) -> bool:
        return isinstance(value, CountingModel)

    def fields(self, annotation: object) -> tuple[ModelField, ...]:
        return ()

    def dump(
        self,
        value: object,
        *,
        mode: ModelDumpMode,
        registry: ModelAdapterRegistry,
    ) -> object:
        assert isinstance(value, CountingModel)
        value.calls += 1
        return {"value": 1}

    def load[T](
        self,
        annotation: type[T],
        value: object,
        *,
        registry: ModelAdapterRegistry,
    ) -> T:
        return annotation()


def test_serialization_occurs_once_and_prepared_types_have_no_logical_fields() -> None:
    model = CountingModel()
    request = prepare(
        Contract(parameters=(Path("payment_id"),), body=JsonBody()),
        path={"payment_id": "1"},
        body=model,
        has_body=True,
        models=default_model_adapters().with_adapter(CountingModelAdapter()),
    )
    assert model.calls == 1
    assert not {"json", "data", "cookies", "files"} & {
        field.name for field in dataclasses.fields(UnsignedPreparedRequest)
    }
    final = request.finalize()
    assert isinstance(final, PreparedRequest)
    with pytest.raises(dataclasses.FrozenInstanceError):
        final.target = b"/changed"  # type: ignore[misc]


def test_replayable_stream_requires_a_factory_artifact() -> None:
    descriptor = ReplayableStreamBody(content_type="application/octet-stream")
    good = ReplayableBodyStream(lambda: BytesIO(b"stream"), 6)
    request = prepare(
        Contract(parameters=(Path("payment_id"),), body=descriptor),
        path={"payment_id": "1"},
        body=good,
        has_body=True,
    )
    assert request.body is good
    with pytest.raises(BindingError, match="ReplayableBodyStream"):
        prepare(
            Contract(parameters=(Path("payment_id"),), body=descriptor),
            path={"payment_id": "1"},
            body=BytesIO(b"one-shot"),
            has_body=True,
        )
