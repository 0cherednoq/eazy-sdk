from __future__ import annotations

import eazy_sdk.ext as ext


def test_ext_surface_is_the_reviewed_extension_spi() -> None:
    assert ext.__all__ == [
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


def test_ext_surface_does_not_publish_runtime_or_lowering_stages() -> None:
    removed = {
        "DependencyCaches",
        "ExecutionCore",
        "ExecutionRuntime",
        "PreparedRequest",
        "RequestPreparer",
        "bind_plan",
        "compile_dependency_order",
        "compile_layout",
        "compile_signatures",
        "resolve_requirements",
        "sign_prepared",
    }

    assert removed.isdisjoint(ext.__all__)
    assert all(not hasattr(ext, name) for name in removed)
