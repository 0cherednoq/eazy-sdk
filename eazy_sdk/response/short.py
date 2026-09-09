"""Short response markers: ``Payload[T]`` is ``Annotated[T, markers.Payload()]``.

Private module; the alias is published from ``eazy_sdk.response``. The form is ``TypeVar`` plus
``Annotated`` rather than a PEP 695 ``type`` statement for the reason phase 50 recorded on the
request side: ``get_type_hints(include_extras=True)`` substitutes a ``TypeVar`` alias into a
plain ``Annotated[DocumentPage, Payload()]``, so the reader sees one annotation whichever form
the author wrote, while a ``TypeAliasType`` would have to be unrolled by hand at every reader.
"""

from typing import Annotated, TypeVar

from eazy_sdk.response import markers

_T = TypeVar("_T")

Payload = Annotated[_T, markers.Payload()]
