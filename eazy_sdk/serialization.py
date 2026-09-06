"""Serialization implementation: which library and which backend, declared once.

Four transforms turn a declaration into bytes: model ↔ structure, structure ↔ bytes,
public schema ↔ wire schema, and bytes ↔ bytes on the wire. The last two are contract —
they decide what the server receives, so they belong to the operation. The first two have
an implementation half that does not change the bytes at all: which model library reads the
annotation, which JSON backend encodes the structure. That half is declared here, once on
the SDK root, and never in the client: a client delivers bytes, it does not decide how they
look.

The response has a fifth transform the request does not: a document that is not already a
structure (HTML, XML) is parsed, and only then are the model's fields read out of it by
selector. The selectors are the contract — they say where the price lives — and the parser
is implementation, so it is declared here too. What makes it more than a preference is that
backends are not interchangeable by selector language: selectolax reads CSS and not XPath.
An operation whose selector the chosen backend cannot speak is rejected at compile time,
because the runtime alternative — an empty extraction — reads like a server that stopped
sending the field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from eazy_sdk.core.errors import EazySdkError
from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
from eazy_sdk.request.wire import DEFAULT_JSON_BACKEND, JsonBackend


class BackendCapabilityError(EazySdkError, TypeError):
    """A declared operation asks for bytes the chosen backend cannot produce or read."""


@runtime_checkable
class SelectorMarker(Protocol):
    """One selector as an operation declares it: a language and an expression in it."""

    @property
    def language(self) -> str: ...

    @property
    def expression(self) -> str: ...


class DocumentNode(Protocol):
    """One node of a parsed document, addressed by the operation's selectors."""

    def values(self, marker: SelectorMarker) -> tuple[str, ...]: ...

    def nodes(self, marker: SelectorMarker) -> tuple[DocumentNode, ...]: ...


@runtime_checkable
class DocumentBackend(Protocol):
    """The parser that builds a document out of response bytes — implementation.

    ``selector_languages`` is the half that is not a preference: a backend that does not
    speak ``"xpath"`` makes every operation selecting by XPath a compile error.
    ``media_types`` is the other half: an HTML parser and an XML parser can sit side by
    side, and the response says which of them reads it.
    """

    @property
    def name(self) -> str: ...

    @property
    def selector_languages(self) -> frozenset[str]: ...

    @property
    def media_types(self) -> frozenset[str]: ...

    def parse(self, data: bytes | str) -> DocumentNode: ...


def accepts_media(backend: DocumentBackend, media_type: str | None) -> bool:
    """Whether ``backend`` reads that media type, honouring the ``+xml`` suffix rule."""

    if media_type is None:
        return True
    media = media_type.split(";", 1)[0].strip().lower()
    if media in backend.media_types:
        return True
    return media.endswith("+xml") and any(
        declared.endswith("xml") for declared in backend.media_types
    )


@dataclass(frozen=True, slots=True)
class Serialization:
    """The libraries an SDK serializes with, declared once on its root."""

    models: ModelAdapterRegistry = field(default_factory=default_model_adapters)
    json: JsonBackend = DEFAULT_JSON_BACKEND
    documents: tuple[DocumentBackend, ...] = ()
    """Empty leaves the choice to the extraction plugin; several are chosen by response media."""

    def __post_init__(self) -> None:
        if not isinstance(self.models, ModelAdapterRegistry):
            raise TypeError("Serialization.models must be a ModelAdapterRegistry")
        if not isinstance(self.json, JsonBackend):
            raise TypeError("Serialization.json must implement the JsonBackend protocol")
        if not isinstance(self.documents, tuple) or any(
            not isinstance(item, DocumentBackend) for item in self.documents
        ):
            raise TypeError(
                "Serialization.documents is a tuple of DocumentBackend implementations"
            )

    def document_backend(
        self, media_type: str | None, *, operation_id: str
    ) -> DocumentBackend | None:
        """The parser that reads this response, or ``None`` to leave the plugin its default.

        One configured backend always reads: an SDK that declares a parser meant it. Several
        are told apart by the response media, which is the only thing that distinguishes an
        HTML page from an XML document on the same service.
        """

        if not self.documents:
            return None
        if len(self.documents) == 1:
            return self.documents[0]
        for backend in self.documents:
            if accepts_media(backend, media_type):
                return backend
        configured = ", ".join(
            f"{backend.name} ({', '.join(sorted(backend.media_types))})"
            for backend in self.documents
        )
        raise BackendCapabilityError(
            f"no document backend accepts {media_type!r} for {operation_id!r}; "
            f"configured: {configured}"
        )


__all__ = [
    "BackendCapabilityError",
    "DocumentBackend",
    "DocumentNode",
    "SelectorMarker",
    "Serialization",
    "accepts_media",
]
