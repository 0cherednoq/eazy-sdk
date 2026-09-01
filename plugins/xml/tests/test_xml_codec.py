from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, cast

import msgspec
import pytest
from eazy_sdk_xml import ElementTreeXmlCodec, XmlBody, XmlResponse
from pydantic import BaseModel

from eazy_sdk import Client, SyncApi, api
from eazy_sdk.response import (
    Extracted,
    Headers,
    NormalizedResponse,
    ResponseContext,
    Responses,
    Success,
)
from eazy_sdk.testing import RecordingHandler

XML_BODY = XmlBody(ElementTreeXmlCodec(root_name="user"))


@dataclass
class DataclassUser:
    id: int
    roles: list[str]


class PydanticUser(BaseModel):
    id: int
    roles: list[str]


class MsgspecUser(msgspec.Struct):
    id: int
    roles: list[str]


@pytest.mark.parametrize("model", [DataclassUser, PydanticUser, MsgspecUser])
def test_xml_body_and_response_use_the_model_adapter_matrix(model: type[Any]) -> None:
    codec = ElementTreeXmlCodec(root_name="user")
    responses: Responses[object] = Responses(
        success=(Success(200, Extracted(model, using=XmlResponse(codec))),)
    )

    class XmlApi(SyncApi):
        @api.post("/xml", operation_id="xml", responses=responses)
        def xml(self, *, body: Annotated[object, XML_BODY]) -> object:
            raise NotImplementedError

    body = model(id=7, roles=["admin", "reader"])
    with Client(base_url="https://api.test", handler=RecordingHandler()) as client:
        prepared = XmlApi(client).xml.prepare(body=body)
    assert prepared.encoded_body is not None
    response = NormalizedResponse(
        status_code=200,
        url="https://api.test/user",
        method="POST",
        headers=Headers((("content-type", "application/xml"),)),
        body=prepared.encoded_body,
    )
    outcome = responses.inspect(ResponseContext(response))
    result = cast(Any, outcome.unwrap())
    assert result == body
    assert result.id == 7
    assert result.roles == ["admin", "reader"]
