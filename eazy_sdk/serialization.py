"""Serialization implementation: which library and which backend, declared once.

Four transforms turn a declaration into bytes: model ↔ structure, structure ↔ bytes,
public schema ↔ wire schema, and bytes ↔ bytes on the wire. The last two are contract —
they decide what the server receives, so they belong to the operation. The first two have
an implementation half that does not change the bytes at all: which model library reads the
annotation, which JSON backend encodes the structure. That half is declared here, once on
the SDK root, and never in the client: a client delivers bytes, it does not decide how they
look.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eazy_sdk.models import ModelAdapterRegistry, default_model_adapters
from eazy_sdk.request import WireProfile


@dataclass(frozen=True, slots=True)
class Serialization:
    """The model adapters an SDK uses, declared on its root.

    ``profile`` is the current :class:`~eazy_sdk.request.WireProfile`, of which the runtime
    reads only ``protocol``; phase 48 replaces it with an explicit encoding contract on the
    operation.
    """

    models: ModelAdapterRegistry = field(default_factory=default_model_adapters)
    profile: WireProfile | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.models, ModelAdapterRegistry):
            raise TypeError("Serialization.models must be a ModelAdapterRegistry")
        if self.profile is not None and not isinstance(self.profile, WireProfile):
            raise TypeError("Serialization.profile must be a WireProfile")


__all__ = ["Serialization"]
