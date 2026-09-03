"""Offline HTML extraction and legacy inspection utilities."""

from .schema import (
    CSS,
    ExtractionCompileError,
    ExtractionError,
    ExtractionField,
    ExtractionSchema,
    HtmlDocument,
    Scope,
    XPath,
    compile_extraction_schema,
    parse_html,
)

__all__ = [
    "CSS",
    "ExtractionCompileError",
    "ExtractionError",
    "ExtractionField",
    "ExtractionSchema",
    "HtmlDocument",
    "Scope",
    "XPath",
    "compile_extraction_schema",
    "parse_html",
]
