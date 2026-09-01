"""Implementation contracts for application-defined Eazy SDK extensions.

Declarative authoring objects live in their feature namespaces.  This module is intentionally
small: it contains only protocols, immutable contexts/results, and factories needed to implement
custom codecs, response parsers/extractors, request scopes, and procedural signing hooks.
"""

from eazy_sdk._internal import OperationIdentity, RequestScope, ScopeContext
from eazy_sdk.codecs import BodyCodec, EncodeContext, ScalarCodec, ScalarEncodeContext
from eazy_sdk.request.signatures import (
    CustomCanonicalizer,
    CustomRequestSigner,
    SignatureResult,
    SigningInput,
    custom_base,
    custom_signature,
    read_set,
    whole_prepared_request,
)
from eazy_sdk.response.cases import (
    BoundResponseExtractor,
    BoundResponseParser,
    Malformed,
    NoMatch,
    ParseAttempt,
    ParsedValue,
    ResponseExtractor,
    ResponseParser,
)

__all__ = [
    "BodyCodec",
    "BoundResponseExtractor",
    "BoundResponseParser",
    "CustomCanonicalizer",
    "CustomRequestSigner",
    "EncodeContext",
    "Malformed",
    "NoMatch",
    "OperationIdentity",
    "ParseAttempt",
    "ParsedValue",
    "RequestScope",
    "ResponseExtractor",
    "ResponseParser",
    "ScalarCodec",
    "ScalarEncodeContext",
    "ScopeContext",
    "SignatureResult",
    "SigningInput",
    "custom_base",
    "custom_signature",
    "read_set",
    "whole_prepared_request",
]
