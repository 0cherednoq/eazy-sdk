from __future__ import annotations

import pytest

from eazy_sdk.exceptions import HeaderValidationError
from eazy_sdk.response import NormalizedResponse
from eazy_sdk.response.headers import Headers, ResponseHeader

pytestmark = pytest.mark.unit


def test_empty_headers_have_mapping_semantics() -> None:
    headers = Headers()
    assert len(headers) == 0
    assert list(headers) == []
    assert headers.getall("missing") == ()
    with pytest.raises(KeyError, match="missing"):
        _ = headers["missing"]


def test_headers_are_case_insensitive_and_preserve_duplicate_lines() -> None:
    headers = Headers(((b"X-Test", b"one"), ("x-test", "two"), ("Other", "value")))
    assert headers["X-TEST"] == "one, two"
    assert headers.getall("x-test") == ("one", "two")
    assert headers.multi_items() == (("x-test", "one"), ("x-test", "two"), ("other", "value"))
    assert list(headers) == ["x-test", "other"]


def test_documented_response_header_parses_typed_values() -> None:
    response: NormalizedResponse[object] = NormalizedResponse(
        200,
        "https://api.test",
        "GET",
        (("X-Count", "42"),),
        b"",
    )
    assert ResponseHeader[int]("X-Count", int, required=True).parse(response) == 42


def test_required_and_invalid_response_headers_raise_meaningful_errors() -> None:
    missing: NormalizedResponse[object] = NormalizedResponse(
        200, "https://api.test", "GET", (), b""
    )
    with pytest.raises(HeaderValidationError, match="Required response header"):
        ResponseHeader("X-Required", required=True).parse(missing)

    invalid: NormalizedResponse[object] = NormalizedResponse(
        200, "https://api.test", "GET", (("X-Count", "many"),), b""
    )
    with pytest.raises(HeaderValidationError, match="failed validation"):
        ResponseHeader[int]("X-Count", int).parse(invalid)
