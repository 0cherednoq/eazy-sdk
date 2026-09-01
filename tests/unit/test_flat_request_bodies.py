from __future__ import annotations

from typing import Annotated, Any, NotRequired, TypedDict, cast

import pytest

from eazy_sdk import ApiDefaults, OperationBindingError, SyncApi, api
from eazy_sdk._internal import (
    CompiledContract,
    OperationValues,
    Set,
    ValuePatch,
    apply_patch_atomic,
)
from eazy_sdk.ext import BufferedBody, RequestPreparer, UnsignedPreparedRequest
from eazy_sdk.request import (
    DelimitedScalarCodec,
    Form,
    JsonBody,
    JsonField,
    MultipartPart,
    Part,
    WireOptions,
)
from eazy_sdk.response import Responses


class FlatJsonRequest(TypedDict):
    name: str
    count: int
    note: NotRequired[str]


class BodyApi(SyncApi):
    @api.post("/body", operation_id="jsonBody", responses=Responses(success=()))
    def json_body(
        self,
        *,
        ticket_count: Annotated[int, JsonField("ticketCount")],
        visitor_name: Annotated[str, JsonField("visitorName")],
        note: Annotated[str | None, JsonField()] = None,
    ) -> object:
        raise NotImplementedError

    @api.post("/body", operation_id="formBody", responses=Responses(success=()))
    def form_body(
        self,
        *,
        username: Annotated[str, Form()],
        scopes: Annotated[
            list[str] | None,
            Form("scope", codec=DelimitedScalarCodec()),
        ] = None,
    ) -> object:
        raise NotImplementedError

    @api.post("/body", operation_id="multipartBody", responses=Responses(success=()))
    def multipart_body(
        self,
        *,
        document: Annotated[bytes | MultipartPart, Part("document")],
        title: Annotated[str | None, Part()] = None,
    ) -> object:
        raise NotImplementedError

    @api.post("/body", operation_id="typedDictBody", responses=Responses(success=()))
    def typed_dict_body(
        self,
        *,
        request: Annotated[FlatJsonRequest, JsonBody()],
    ) -> object:
        raise NotImplementedError
def _compiled(name: str, *, body_order: tuple[str, ...] | None = None) -> CompiledContract[Any]:
    descriptor = getattr(BodyApi, name)
    declaration = descriptor.resolve(ApiDefaults())
    if body_order is not None:
        from dataclasses import replace

        declaration = replace(declaration, wire=WireOptions(body_order=body_order))
    return cast(CompiledContract[Any], declaration.compile())


def _prepare(
    name: str, request: dict[str, object], *, boundary: str | None = None
) -> UnsignedPreparedRequest:
    compiled = _compiled(name)
    values = OperationValues.from_bound(compiled.plan.shape, compiled.bind_input(request))
    return RequestPreparer("https://example.test").prepare(
        compiled,
        values,
        boundary=boundary,
    )


def _buffered(prepared: UnsignedPreparedRequest) -> BufferedBody:
    assert isinstance(prepared.body, BufferedBody)
    return prepared.body


def test_flat_json_preserves_declaration_order_aliases_and_explicit_none() -> None:
    body = _buffered(
        _prepare(
            "json_body",
            {"ticket_count": 2, "visitor_name": "Ada", "note": None},
        )
    )
    assert body.content == b'{"ticketCount":2,"visitorName":"Ada","note":null}'


def test_flat_json_omits_absent_optional_field() -> None:
    body = _buffered(_prepare("json_body", {"ticket_count": 2, "visitor_name": "Ada"}))
    assert body.content == b'{"ticketCount":2,"visitorName":"Ada"}'


def test_typed_dict_root_body_is_validated_and_serialized_as_a_mapping() -> None:
    body = _buffered(
        _prepare("typed_dict_body", {"request": {"name": "Ada", "count": 2}})
    )
    assert body.content == b'{"name":"Ada","count":2}'

    compiled = _compiled("typed_dict_body")
    with pytest.raises(OperationBindingError) as captured:
        OperationValues.from_bound(
            compiled.plan.shape,
            compiled.bind_input({"request": {"name": "Ada"}}),
        )
    assert captured.value.code == "invalid_value"
    assert captured.value.field == "request.count"

    with pytest.raises(OperationBindingError) as captured:
        compiled.bind_input({"request": {"name": "Ada", "count": "2"}})
    assert captured.value.code == "invalid_value"
    assert captured.value.field == "request"


def test_flat_form_collection_uses_one_explicit_scalar_value() -> None:
    body = _buffered(_prepare("form_body", {"username": "ada", "scopes": ["read", "write"]}))
    assert body.content == b"username=ada&scope=read%2Cwrite"


def test_flat_multipart_supports_bytes_and_rich_parts() -> None:
    body = _buffered(
        _prepare(
            "multipart_body",
            {
                "title": "Map",
                "document": MultipartPart(
                    b"binary", filename="map.pdf", content_type="application/pdf"
                ),
            },
            boundary="fixture",
        )
    )
    assert body.content == (
        b'--fixture\r\nContent-Disposition: form-data; name="document"; '
        b'filename="map.pdf"\r\nContent-Type: application/pdf\r\n\r\nbinary\r\n'
        b'--fixture\r\nContent-Disposition: form-data; name="title"\r\n\r\nMap\r\n'
        b"--fixture--\r\n"
    )


def test_flat_body_field_can_be_patched_before_preparation() -> None:
    compiled = _compiled("json_body")
    values = OperationValues.from_bound(
        compiled.plan.shape,
        compiled.bind_input({"ticket_count": 1, "visitor_name": "before"}),
    )
    changed = apply_patch_atomic(
        values,
        ValuePatch((Set(compiled.input_slots["visitor_name"], "after"),)),
    )
    prepared = RequestPreparer("https://example.test").prepare(compiled, changed)
    assert isinstance(prepared.body, BufferedBody)
    assert prepared.body.content == b'{"ticketCount":1,"visitorName":"after"}'


def test_missing_required_flat_body_field_fails_during_binding() -> None:
    compiled = _compiled("json_body")
    with pytest.raises(OperationBindingError) as captured:
        OperationValues.from_bound(
            compiled.plan.shape,
            compiled.bind_input({"visitor_name": "Ada"}),
        )
    assert captured.value.code == "missing_required"
    assert captured.value.field == "ticket_count"


def test_explicit_flat_body_order_uses_wire_names() -> None:
    compiled = _compiled("json_body", body_order=("visitorName", "ticketCount", "note"))
    values = OperationValues.from_bound(
        compiled.plan.shape,
        compiled.bind_input({"ticket_count": 2, "visitor_name": "Ada"}),
    )
    prepared = RequestPreparer("https://example.test").prepare(compiled, values)
    assert isinstance(prepared.body, BufferedBody)
    assert prepared.body.content == b'{"visitorName":"Ada","ticketCount":2}'
