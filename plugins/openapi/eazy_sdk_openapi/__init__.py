"""OpenAPI SDK generation targeting Eazy SDK contracts."""

from eazy_sdk_openapi.compatibility import (
    CompatibilityIssue,
    CompatibilityReport,
    analyze_openapi,
)
from eazy_sdk_openapi.generator import (
    GenerationConfig,
    ProjectionImport,
    generate_package,
    render_auth,
    render_client,
)
from eazy_sdk_openapi.ir import (
    BodyProjectionIR,
    OpenAPIIR,
    OperationIR,
    Ref,
    SourceIdentity,
    UnsupportedOpenAPIError,
    parse_openapi,
)

__all__ = [
    "BodyProjectionIR",
    "CompatibilityIssue",
    "CompatibilityReport",
    "GenerationConfig",
    "OpenAPIIR",
    "OperationIR",
    "ProjectionImport",
    "Ref",
    "SourceIdentity",
    "UnsupportedOpenAPIError",
    "analyze_openapi",
    "generate_package",
    "parse_openapi",
    "render_auth",
    "render_client",
]
