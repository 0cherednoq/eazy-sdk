from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, cast

import msgspec
import pytest
from eazy_sdk_xml import ElementTreeXmlCodec, XmlBody, XmlResponse
from pydantic import BaseModel

from eazy_sdk import ApiDefaults, SyncApi, api
from eazy_sdk.ext import BufferedBody, RequestPreparer, bind_plan
from eazy_sdk.response import (
    Extracted,
    Headers,
    NormalizedResponse,
    ResponseContext,
    Responses,
    Success,
)

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
    compiled = cast(Any, XmlApi.xml).resolve(ApiDefaults()).compile()
    values = bind_plan(
        compiled.plan,
        compiled.bind_input({"body": body}),
    )
    prepared = RequestPreparer("https://api.test").prepare(compiled, values).finalize()
    assert isinstance(prepared.body, BufferedBody)
    response = NormalizedResponse(
        status_code=200,
        url="https://api.test/user",
        method="POST",
        headers=Headers((("content-type", "application/xml"),)),
        body=prepared.body.content,
    )
    outcome = responses.inspect(ResponseContext(response))
    result = cast(Any, outcome.unwrap())
    assert result == body
    assert result.id == 7
    assert result.roles == ["admin", "reader"]
