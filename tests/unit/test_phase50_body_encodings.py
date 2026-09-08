"""Phase 50: ``Body`` — the named entry for the request body encodings.

The root ``JsonBody`` and its siblings are ``Annotated`` field markers, so ``JsonBody()``
written at ``encoding=`` raises ``TypeError`` from ``typing``. ``Body.json()`` is the form
that constructs the encoding, and these tests pin it to the descriptor defaults.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast

import pytest

import eazy_sdk
import eazy_sdk.request as request_api
from eazy_sdk import Body, BodyProjection
from eazy_sdk.request import markers


class Public(TypedDict):
    value: str


class Wire(TypedDict):
    nested: str


def to_wire(source: Public) -> Wire:
    return {"nested": source["value"]}


def test_body_is_published_from_the_root_and_from_the_request_package() -> None:
    assert "Body" in eazy_sdk.__all__
    assert "Body" in request_api.__all__
    assert request_api.Body is Body


@pytest.mark.parametrize(
    ("made", "constructed"),
    [
        (Body.json(), markers.JsonBody()),
        (Body.form(), markers.FormBody()),
        (Body.multipart(), markers.MultipartBody()),
        (Body.raw(), markers.BytesBody()),
        (Body.stream(), markers.ReplayableStreamBody()),
    ],
)
def test_each_factory_carries_the_descriptor_defaults(made: object, constructed: object) -> None:
    assert type(made) is type(constructed)
    assert made == constructed


def test_options_reach_the_descriptor() -> None:
    assert Body.json(content_type="application/vnd.api+json").content_type == (
        "application/vnd.api+json"
    )
    assert Body.multipart(boundary="----x").boundary == "----x"
    assert Body.raw(content_type="application/octet-stream", content_encoding="gzip") == (
        markers.BytesBody(content_type="application/octet-stream", content_encoding="gzip")
    )
    assert Body.stream(content_type="application/x-ndjson").content_type == "application/x-ndjson"


def test_a_projection_accepts_the_named_encoding() -> None:
    projection = BodyProjection(
        source=Public,
        target=Wire,
        using=to_wire,
        encoding=Body.json(),
        name="phase50.body.v1",
    )
    assert projection.encoding == markers.JsonBody()


def test_the_root_json_body_name_stays_the_field_marker() -> None:
    # The two names are different declarations on purpose: one marks a field, one encodes
    # a projection. Calling the marker is the mistake ``Body`` exists to remove.
    marker = cast(Any, eazy_sdk.JsonBody)
    with pytest.raises(TypeError):
        marker()
