from __future__ import annotations

from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

import pytest
from hypothesis import given
from hypothesis import strategies as st

from eazy_sdk.request.params import QueryParams, append_query
from eazy_sdk.response.headers import Headers

pytestmark = pytest.mark.property

_TEXT = st.text(
    alphabet=st.characters(exclude_categories=cast(Any, ("Cs",))),
    max_size=24,
)
_QUERY_PAIRS = st.lists(st.tuples(_TEXT, _TEXT), max_size=12)
_HEADER_NAMES = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"),
    min_size=1,
    max_size=16,
)
_HEADER_VALUES = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=255,
        exclude_characters="\x7f",
    ),
    max_size=24,
)


@given(_QUERY_PAIRS)
def test_query_render_parse_round_trip_preserves_order_duplicates_and_empty_values(
    pairs: list[tuple[str, str]],
) -> None:
    query = QueryParams(pairs)
    assert parse_qsl(query.render(), keep_blank_values=True, strict_parsing=True) == pairs
    assert query.copy().multi_items() == tuple(pairs)
    assert query.copy().render() == query.render()


@given(_QUERY_PAIRS, _QUERY_PAIRS)
def test_appending_query_preserves_existing_and_new_pair_order(
    existing: list[tuple[str, str]], added: list[tuple[str, str]]
) -> None:
    base = append_query("https://api.test/items", QueryParams(existing))
    combined = append_query(base, QueryParams(added))
    assert parse_qsl(urlsplit(combined).query, keep_blank_values=True) == [*existing, *added]


@given(st.lists(st.tuples(_HEADER_NAMES, _HEADER_VALUES), max_size=12))
def test_header_normalization_is_idempotent_and_lossless_for_field_lines(
    pairs: list[tuple[str, str]],
) -> None:
    once = Headers(pairs)
    twice = Headers(once.multi_items())
    assert twice.multi_items() == once.multi_items()
    assert list(twice) == list(once)
    for name in once:
        assert twice.getall(name.swapcase()) == once.getall(name)
