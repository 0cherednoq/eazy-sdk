"""Phase 50.1: operation classes, short markers, ``op()``, decorator synthesis, ``Omittable``."""

from __future__ import annotations

from typing import Annotated, get_type_hints

import eazy_sdk
from eazy_sdk import request as request_api
from eazy_sdk.request import markers, short


def test_short_marker_equals_annotated_form() -> None:
    """I4: ``Query[int]`` and ``Annotated[int, markers.Query()]`` resolve to one annotation."""

    class Fields:
        short_form: request_api.Query[int]
        long_form: Annotated[int, markers.Query()]
        root_form: eazy_sdk.Query[int]

    hints = get_type_hints(Fields, include_extras=True)
    assert hints["short_form"] == hints["long_form"] == hints["root_form"]
    assert hints["short_form"].__metadata__ == (markers.Query(),)
    for name in short.__dict__:
        if name.startswith("_") or name in {"Annotated", "TypeVar", "markers"}:
            continue
        alias = getattr(short, name)
        assert getattr(request_api, name) is alias
        if name in eazy_sdk.__all__:
            assert getattr(eazy_sdk, name) is alias


def test_markers_module_reexports_every_descriptor() -> None:
    """I4: ``markers`` is the qualified home of every placement descriptor, nothing more."""

    from eazy_sdk.request import descriptors, params

    expected = {
        "Path": params.Path,
        "Query": params.Query,
        "QueryString": params.QueryString,
        "Header": params.Header,
        "Cookie": params.Cookie,
        "JsonField": descriptors.JsonField,
        "Form": descriptors.Form,
        "Part": descriptors.Part,
        "JsonBody": descriptors.JsonBody,
        "FormBody": descriptors.FormBody,
        "MultipartBody": descriptors.MultipartBody,
        "BytesBody": descriptors.BytesBody,
        "ReplayableStreamBody": descriptors.ReplayableStreamBody,
        "BodyProjection": descriptors.BodyProjection,
    }
    assert set(markers.__all__) == set(expected)
    assert markers.__all__ == sorted(markers.__all__)
    for name, descriptor in expected.items():
        assert getattr(markers, name) is descriptor
    # The short form has no name argument, so the descriptors that require one have no alias.
    assert not hasattr(short, "QueryString")
    assert not hasattr(short, "BodyProjection")
    # The root never exposes the descriptor classes under the short names.
    assert eazy_sdk.Path is not params.Path
    assert request_api.Query is not params.Query
