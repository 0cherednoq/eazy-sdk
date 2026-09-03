from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Annotated, Any, cast

import pytest

from eazy_sdk.compile.http_operation import _OperationDeclaration
from eazy_sdk.compile.input import inspect_method_input
from eazy_sdk.core import (
    OperationValues,
)
from eazy_sdk.core.errors import PlanError
from eazy_sdk.request import (
    Cookie,
    Header,
    Path,
    Query,
)
from eazy_sdk.request.prepared import RequestPreparer
from eazy_sdk.response import Responses

pytestmark = pytest.mark.unit


def prepare(
    *parameters: object,
    path: str = "/items/{value}",
    path_values: dict[str, object] | None = None,
    query: dict[str, object] | None = None,
    headers: dict[str, object] | None = None,
    cookies: dict[str, object] | None = None,
) -> Any:
    hints: dict[str, object] = {}
    signature_parameters = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    request: dict[str, object] = {}
    sources = {
        Path: path_values or {},
        Query: query or {},
        Header: headers or {},
        Cookie: cookies or {},
    }
    for index, parameter in enumerate(parameters):
        key = f"field_{index}"
        hints[key] = Annotated[object, parameter]
        signature_parameters.append(
            inspect.Parameter(
                key,
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=object,
            )
        )
        for descriptor_type, source_values in sources.items():
            if isinstance(parameter, descriptor_type):
                name = cast(str, cast(Any, parameter).name)
                if name in source_values:
                    request[key] = source_values[name]
                break
    signature = inspect.Signature(signature_parameters)
    input_schema = inspect_method_input(
        signature,
        hints,
        operation_id="serialization",
        path=path,
        self_parameter="self",
    )
    declaration: _OperationDeclaration[object] = _OperationDeclaration(
        operation_id="serialization",
        method="GET",
        path=path,
        input_fields=input_schema.fields,
        input_schema=input_schema,
        result_type=object,
        responses=Responses(success=()),
        raw_response=True,
    )
    compiled = declaration.compile()
    bound = compiled.bind_input(request)
    values = OperationValues.from_bound(compiled.plan.shape, bound)
    return RequestPreparer("https://api.example").prepare(compiled, values)


@pytest.mark.parametrize(
    ("parameter", "value", "expected"),
    [
        (Path("value"), "a/b c", "/items/a%2Fb%20c"),
        (Path("value", style="label"), ["red", "blue"], "/items/.red,blue"),
        (
            Path("value", style="label", explode=True),
            ["red", "blue"],
            "/items/.red.blue",
        ),
        (
            Path("value", style="matrix", explode=True),
            ["red", "blue"],
            "/items/;value=red;value=blue",
        ),
    ],
)
def test_path_parameter_styles_are_reflected_in_the_wire_target(
    parameter: Path, value: object, expected: str
) -> None:
    request = prepare(parameter, path_values={"value": value})
    assert request.target == expected.encode("ascii")


@pytest.mark.parametrize(
    ("parameter", "value", "expected"),
    [
        (
            Query("item", explode=False),
            ["a b", "привет"],
            b"/items?item=a%20b%2C%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82",
        ),
        (Query("item", explode=False), ["a", "b"], b"/items?item=a%2Cb"),
        (
            Query("item", style="spaceDelimited"),
            ["a", "b"],
            b"/items?item=a%20b",
        ),
        (
            Query("item", style="pipeDelimited"),
            ["a", "b"],
            b"/items?item=a%7Cb",
        ),
        (
            Query("filter", style="deepObject"),
            {"name": "a b", "active": True},
            b"/items?filter[name]=a%20b&filter[active]=true",
        ),
        (Query("empty"), None, b"/items?empty="),
        (Query("flag"), False, b"/items?flag=false"),
        (Query("count"), 3, b"/items?count=3"),
        (
            Query("redirect", allow_reserved=True),
            "https://other.test/a?x=1",
            b"/items?redirect=https://other.test/a?x=1",
        ),
    ],
)
def test_query_serialization_preserves_cardinality_types_and_encoding(
    parameter: Query, value: object, expected: bytes
) -> None:
    name = cast(str, parameter.name)
    request = prepare(parameter, path="/items", query={name: value})
    assert request.target == expected


@pytest.mark.parametrize(
    ("parameter", "value", "expected"),
    [
        (Header("X-Value", explode=True), {"a": 1, "b": 2}, b"a=1,b=2"),
        (Header("X-Value"), {"a": 1, "b": 2}, b"a,1,b,2"),
    ],
)
def test_header_parameter_serialization(parameter: Header, value: object, expected: bytes) -> None:
    name = cast(str, parameter.name)
    request = prepare(parameter, path="/items", headers={name: value})
    fields = [(field.name, field.value) for field in request.headers]
    assert (name.encode("ascii"), expected) in fields


def test_header_sequence_preserves_duplicate_field_lines_and_order() -> None:
    request = prepare(
        Header("X-Value"),
        path="/items",
        headers={"X-Value": ["a", "b"]},
    )
    assert [field.value for field in request.headers if field.name == b"X-Value"] == [b"a", b"b"]


@pytest.mark.parametrize(
    ("parameter", "value", "expected"),
    [
        (Cookie("value"), ["a", "b"], b"value=a,b"),
        (Cookie("value", explode=True), {"a": 1, "b": 2}, b"value=a=1,b=2"),
        (Cookie("value", explode=False), {"a": 1, "b": 2}, b"value=a,1,b,2"),
    ],
)
def test_cookie_parameter_serialization(parameter: Cookie, value: object, expected: bytes) -> None:
    name = cast(str, parameter.name)
    request = prepare(parameter, path="/items", cookies={name: value})
    cookie = next(field.value for field in request.headers if field.name.lower() == b"cookie")
    assert cookie == expected


def test_case_colliding_request_headers_are_rejected() -> None:
    with pytest.raises(PlanError, match="duplicate header"):
        prepare(
            Header("X-Trace"),
            Header("x-trace"),
            path="/items",
            headers={"X-Trace": "one", "x-trace": "two"},
        )


@pytest.mark.parametrize("value", ["line\rbreak", "line\nbreak", "line\x00break"])
def test_request_header_control_character_injection_is_rejected(value: str) -> None:
    with pytest.raises(PlanError, match="invalid request header"):
        prepare(
            Header("X-Value"),
            path="/items",
            headers={"X-Value": value},
        )


@dataclass(frozen=True)
class Unsupported:
    value: str


def test_unsupported_header_name_is_reported_as_a_plan_error() -> None:
    with pytest.raises(PlanError, match="invalid request header name"):
        prepare(
            Header("X-Привет"),
            path="/items",
            headers={"X-Привет": Unsupported("value")},
        )
