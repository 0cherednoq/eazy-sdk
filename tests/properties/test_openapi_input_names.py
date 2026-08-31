from __future__ import annotations

import keyword

from eazy_sdk_openapi.generator import _normalize_request_identities
from hypothesis import given
from hypothesis import strategies as st


@given(
    st.lists(
        st.tuples(
            st.sampled_from(["path", "query", "header", "cookie", "body"]),
            st.text(max_size=30),
        ),
        max_size=30,
        unique=True,
    )
)
def test_generated_request_keys_are_unique_python_identifiers(
    identities: list[tuple[str, str]],
) -> None:
    names = _normalize_request_identities(identities)

    assert len(names) == len(set(names))
    assert all(name.isidentifier() for name in names)
    assert all(not keyword.iskeyword(name) for name in names)
    assert not ({"self", "options", "request"} & set(names))
