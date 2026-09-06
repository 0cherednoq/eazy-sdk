"""Offline HTML extraction and legacy inspection utilities."""

from .schema import (
    CSS,
    DEFAULT_HTML_BACKEND,
    ExtractionCompileError,
    ExtractionError,
    ExtractionField,
    ExtractionSchema,
    HtmlDocument,
    ParselBackend,
    ParselNode,
    Scope,
    XPath,
    compile_extraction_schema,
    parse_html,
)

__all__ = [
    "CSS",
    "DEFAULT_HTML_BACKEND",
    "ExtractionCompileError",
    "ExtractionError",
    "ExtractionField",
    "ExtractionSchema",
    "HtmlDocument",
    "ParselBackend",
    "ParselNode",
    "Scope",
    "XPath",
    "compile_extraction_schema",
    "parse_html",
]
