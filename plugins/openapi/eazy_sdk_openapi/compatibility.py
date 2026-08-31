"""Observable compatibility reporting for executable OpenAPI code generation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .ir import select_request_media


@dataclass(frozen=True, slots=True)
class CompatibilityIssue:
    """A declared OpenAPI behavior that is only partially represented or omitted."""

    pointer: str
    feature: str
    support: Literal["partial", "ignored"]
    detail: str


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """Deterministic summary of the executable surface emitted from a document."""

    openapi: str
    path_operations: int
    generated_operations: int
    issues: tuple[CompatibilityIssue, ...]

    @property
    def fully_supported(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "openapi": self.openapi,
            "path_operations": self.path_operations,
            "generated_operations": self.generated_operations,
            "fully_supported": self.fully_supported,
            "issues": [asdict(issue) for issue in self.issues],
        }


_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def analyze_openapi(document: Mapping[str, Any]) -> CompatibilityReport:
    """Report semantics not represented by the generated outbound HTTP client.

    The report is deliberately about executable behavior. Descriptive fields such as
    examples, summaries and documentation links are not code-generation gaps.
    """
    issues: list[CompatibilityIssue] = []
    paths = document.get("paths", {})
    operation_count = 0

    root_servers = document.get("servers")
    if isinstance(root_servers, list) and len(root_servers) > 1:
        issues.append(
            CompatibilityIssue(
                "#/servers",
                "server-alternatives",
                "partial",
                "only the first server is represented",
            )
        )

    schemes = _mapping(document.get("components", {})).get("securitySchemes", {})
    for name, raw_scheme in _mapping(schemes).items():
        scheme = _mapping(raw_scheme)
        if scheme.get("type") in {"oauth2", "openIdConnect"}:
            issues.append(
                CompatibilityIssue(
                    f"#/components/securitySchemes/{_escape(str(name))}",
                    "oauth-token-acquisition",
                    "partial",
                    "bearer injection is generated; token acquisition, refresh and scopes "
                    "are application concerns",
                )
            )

    for raw_path, raw_path_item in _mapping(paths).items():
        path_pointer = f"#/paths/{_escape(str(raw_path))}"
        path_item = _mapping(_resolve(document, raw_path_item))
        for method, raw_operation in path_item.items():
            if str(method).lower() not in _METHODS:
                continue
            operation_count += 1
            pointer = f"{path_pointer}/{str(method).lower()}"
            operation = _mapping(_resolve(document, raw_operation))
            _operation_issues(document, operation, pointer, issues)

    webhooks = document.get("webhooks")
    if isinstance(webhooks, Mapping) and webhooks:
        issues.append(
            CompatibilityIssue(
                "#/webhooks",
                "webhooks",
                "ignored",
                "inbound webhook handlers are outside the outbound client generator",
            )
        )

    return CompatibilityReport(
        openapi=str(document.get("openapi", "")),
        path_operations=operation_count,
        generated_operations=operation_count,
        issues=tuple(sorted(issues, key=lambda item: (item.pointer, item.feature))),
    )


def _operation_issues(
    document: Mapping[str, Any],
    operation: Mapping[str, Any],
    pointer: str,
    issues: list[CompatibilityIssue],
) -> None:
    callbacks = operation.get("callbacks")
    if isinstance(callbacks, Mapping) and callbacks:
        issues.append(
            CompatibilityIssue(
                f"{pointer}/callbacks",
                "callbacks",
                "ignored",
                "inbound callback handlers are outside the outbound client generator",
            )
        )

    servers = operation.get("servers")
    if isinstance(servers, list) and len(servers) > 1:
        issues.append(
            CompatibilityIssue(
                f"{pointer}/servers",
                "server-alternatives",
                "partial",
                "only the first server is represented",
            )
        )

    raw_body = operation.get("requestBody")
    body = _mapping(_resolve(document, raw_body))
    content = _mapping(body.get("content"))
    if len(content) > 1:
        chosen = select_request_media(content)
        omitted = [str(media) for media in content if str(media) != chosen]
        issues.append(
            CompatibilityIssue(
                f"{pointer}/requestBody/content",
                "request-media-alternatives",
                "partial",
                f"generated method uses {chosen}; omitted alternatives: {', '.join(omitted)}",
            )
        )

    for status, raw_response in _mapping(operation.get("responses")).items():
        response_pointer = f"{pointer}/responses/{_escape(str(status))}"
        response = _mapping(_resolve(document, raw_response))
        if _mapping(response.get("headers")):
            issues.append(
                CompatibilityIssue(
                    f"{response_pointer}/headers",
                    "response-headers",
                    "ignored",
                    "typed response-header extraction is not emitted",
                )
            )
        if _mapping(response.get("links")):
            issues.append(
                CompatibilityIssue(
                    f"{response_pointer}/links",
                    "response-links",
                    "ignored",
                    "OpenAPI links are not emitted as follow-up operations",
                )
            )
        for media_type in _mapping(response.get("content")):
            media = str(media_type)
            if not _structured_media(media):
                issues.append(
                    CompatibilityIssue(
                        f"{response_pointer}/content/{_escape(media)}",
                        "raw-response-media",
                        "partial",
                        f"{media} is returned as bytes; its schema is not decoded",
                    )
                )


def _structured_media(media_type: str) -> bool:
    return (
        media_type == "application/json"
        or media_type.endswith("+json")
        or media_type.startswith("text/")
        or media_type in {"application/jsonl", "application/x-ndjson", "application/json-seq"}
        or media_type.endswith("+json-seq")
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _resolve(document: Mapping[str, Any], value: Any) -> Any:
    if not isinstance(value, Mapping) or "$ref" not in value:
        return value
    ref = value.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return value
    current: Any = document
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or key not in current:
            return value
        current = current[key]
    return current


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
