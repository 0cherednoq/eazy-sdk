"""Request authoring markers, signatures, wire declarations and immutable prepared artifacts.

``Path``, ``Query`` and the other placement names here are the short form, ``Query[int]``:
an alias over ``Annotated``. The descriptor classes behind them, which take a wire name and
options, are reached through :mod:`eazy_sdk.request.markers`.
"""

from eazy_sdk.codecs import (
    BodyCodec,
    DefaultScalarCodec,
    DelimitedScalarCodec,
    EncodeContext,
    ScalarCodec,
    ScalarEncodeContext,
)

from . import markers
from .descriptors import BodyProjection, MultipartPart, RequestBody
from .params import QueryString
from .short import (
    BytesBody,
    Cookie,
    Form,
    FormBody,
    Header,
    JsonBody,
    JsonField,
    MultipartBody,
    Part,
    Path,
    Query,
    ReplayableStreamBody,
)
from .signatures import (
    DeclarativeSignature,
    HmacSha256,
    SigningKey,
    SigningKeyRequirement,
    body_digest,
    body_output,
    canonical_json,
    cookie_output,
    header,
    header_output,
    hmac_sha256,
    join,
    literal,
    method,
    path,
    previous_signature,
    query,
    query_output,
    target,
    wire_body,
)
from .wire import (
    FieldOrder,
    JsonPolicy,
    QueryCodec,
    Wire,
)

__all__ = [
    "BodyCodec",
    "BodyProjection",
    "BytesBody",
    "Cookie",
    "DeclarativeSignature",
    "DefaultScalarCodec",
    "DelimitedScalarCodec",
    "EncodeContext",
    "FieldOrder",
    "Form",
    "FormBody",
    "Header",
    "HmacSha256",
    "JsonBody",
    "JsonField",
    "JsonPolicy",
    "MultipartBody",
    "MultipartPart",
    "Part",
    "Path",
    "Query",
    "QueryCodec",
    "QueryString",
    "ReplayableStreamBody",
    "RequestBody",
    "ScalarCodec",
    "ScalarEncodeContext",
    "SigningKey",
    "SigningKeyRequirement",
    "Wire",
    "body_digest",
    "body_output",
    "canonical_json",
    "cookie_output",
    "header",
    "header_output",
    "hmac_sha256",
    "join",
    "literal",
    "markers",
    "method",
    "path",
    "previous_signature",
    "query",
    "query_output",
    "target",
    "wire_body",
]
