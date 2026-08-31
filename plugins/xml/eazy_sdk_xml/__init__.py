"""Optional XML request codec and primitive response extractor for Eazy SDK."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from xml.etree import ElementTree

from eazy_sdk.ext import EncodeContext, Malformed, ParseAttempt, ParsedValue
from eazy_sdk.response import ResponseContext


class XmlDecodeError(ValueError):
    pass


class XmlCodec(Protocol):
    def serialize(self, value: object) -> bytes: ...

    def parse(self, body: bytes) -> object: ...


@dataclass(frozen=True, slots=True)
class ElementTreeXmlCodec:
    root_name: str = "root"
    encoding: str = "utf-8"

    def serialize(self, value: object) -> bytes:
        root = ElementTree.Element(self.root_name)
        _fill(root, value)
        return cast(
            bytes,
            ElementTree.tostring(root, encoding=self.encoding, xml_declaration=True),
        )

    def parse(self, body: bytes) -> object:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise XmlDecodeError("XML body could not be decoded") from exc
        return _element_value(root)


@dataclass(frozen=True, slots=True)
class XmlBody:
    codec: XmlCodec = ElementTreeXmlCodec()
    name: str = "xml"
    media_type: str | None = "application/xml"

    def encode(self, value: object, context: EncodeContext) -> bytes:
        return self.codec.serialize(value)


@dataclass(frozen=True, slots=True)
class XmlResponse:
    codec: XmlCodec = ElementTreeXmlCodec()
    name: str = "xml"

    def bind(self, response: ResponseContext[object]) -> _BoundXmlResponse:
        return _BoundXmlResponse(response, self.codec)


@dataclass(frozen=True, slots=True)
class _BoundXmlResponse:
    response: ResponseContext[object]
    codec: XmlCodec

    def extract(self, model: type[object]) -> ParseAttempt[object]:
        try:
            parsed = self.response.cached(self.codec, lambda: self.codec.parse(self.response.bytes))
            return ParsedValue(parsed)
        except Exception as exc:
            return Malformed(exc)


def _fill(element: ElementTree.Element, value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, list | tuple):
                for repeated in item:
                    child = ElementTree.SubElement(element, str(key))
                    _fill(child, repeated)
            else:
                child = ElementTree.SubElement(element, str(key))
                _fill(child, item)
    elif isinstance(value, list | tuple):
        for item in value:
            child = ElementTree.SubElement(element, "item")
            _fill(child, item)
    elif value is not None:
        element.text = str(value).lower() if isinstance(value, bool) else str(value)


def _element_value(element: ElementTree.Element) -> object:
    children = list(element)
    if not children:
        return element.text or ""
    output: dict[str, object] = {}
    for child in children:
        value = _element_value(child)
        if child.tag not in output:
            output[child.tag] = value
        elif isinstance(output[child.tag], list):
            cast(list[object], output[child.tag]).append(value)
        else:
            output[child.tag] = [output[child.tag], value]
    return output


__all__ = ["ElementTreeXmlCodec", "XmlBody", "XmlCodec", "XmlDecodeError", "XmlResponse"]
