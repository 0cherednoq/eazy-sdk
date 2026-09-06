"""Placement descriptors, qualified: ``markers.Query("caseId")`` when a name or option is needed.

The short form (``Query[int]``, exported from the root and from ``eazy_sdk.request``) is an
alias over ``Annotated`` and carries no arguments. When a field needs a wire name, a style or
a codec, write the descriptor out: ``Annotated[int, markers.Query("per_page", explode=False)]``.
Nothing is defined here; every class lives in ``params`` or ``descriptors``.
"""

from eazy_sdk.request.descriptors import (
    BodyProjection,
    BytesBody,
    Form,
    FormBody,
    JsonBody,
    JsonField,
    MultipartBody,
    Part,
    ReplayableStreamBody,
)
from eazy_sdk.request.params import Cookie, Header, Path, Query, QueryString

__all__ = [
    "BodyProjection",
    "BytesBody",
    "Cookie",
    "Form",
    "FormBody",
    "Header",
    "JsonBody",
    "JsonField",
    "MultipartBody",
    "Part",
    "Path",
    "Query",
    "QueryString",
    "ReplayableStreamBody",
]
