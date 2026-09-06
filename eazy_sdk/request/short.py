"""Short placement markers: ``Query[int]`` is ``Annotated[int, markers.Query()]``.

Private module; the aliases are published from ``eazy_sdk.request`` and the root. The form is
``TypeVar`` plus ``Annotated`` rather than a PEP 695 ``type`` statement on purpose:
``get_type_hints(include_extras=True)`` substitutes a ``TypeVar`` alias into a plain
``Annotated[int, Query()]``, so the compiler sees one annotation whichever form the author
wrote. A ``TypeAliasType`` would have to be unrolled by hand at every reader.
"""

from typing import Annotated, TypeVar

from eazy_sdk.request import markers

_T = TypeVar("_T")

Path = Annotated[_T, markers.Path()]
Query = Annotated[_T, markers.Query()]
Header = Annotated[_T, markers.Header()]
Cookie = Annotated[_T, markers.Cookie()]
JsonField = Annotated[_T, markers.JsonField()]
Form = Annotated[_T, markers.Form()]
Part = Annotated[_T, markers.Part()]
JsonBody = Annotated[_T, markers.JsonBody()]
FormBody = Annotated[_T, markers.FormBody()]
MultipartBody = Annotated[_T, markers.MultipartBody()]
BytesBody = Annotated[_T, markers.BytesBody()]
ReplayableStreamBody = Annotated[_T, markers.ReplayableStreamBody()]
