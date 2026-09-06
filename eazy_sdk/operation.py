"""An HTTP operation as a class: the request is a value, its metadata is ``__http__``.

::

    @dataclass(frozen=True, slots=True, kw_only=True)
    class GetOrder(HttpOperation[Order]):
        __http__ = Http.get("/orders/{order_id}", errors={404: OrderNotFound})

        order_id: Path[str]
        expand: Query[tuple[str, ...]] = ()

    class OrdersApi(SyncApi):
        get_order = op(GetOrder)

The class may be a dataclass, a Pydantic model or a msgspec Struct; the fields carry
placement markers, the model library owns the wire names, and ``__http__`` carries
everything the decorator used to take as keyword arguments — the decorator builds the same
``_HttpSpec`` through the same :class:`Http`, so the two forms cannot drift apart.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict, Unpack, cast, get_args, get_origin

from eazy_sdk.request.wire import EMPTY_WIRE, Wire

if TYPE_CHECKING:
    from eazy_sdk.crypto import PayloadCrypto
    from eazy_sdk.dependencies import Inject
    from eazy_sdk.protection.advanced import SolverRequirement
    from eazy_sdk.request.descriptors import BodyProjection
    from eazy_sdk.response._mapping import ErrorSpec, Selector, SuccessSpec


class _Inherit:
    __slots__ = ()

    def __repr__(self) -> str:
        return "INHERIT"


_INHERIT = _Inherit()
"""The operation says nothing; the router (its MRO) or the root decides."""

_METHOD_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class HttpOperation[T]:
    """Nominal base of an HTTP operation class. Carries no fields and no constructor.

    ``T`` is the default success value: ``HttpOperation[Order]`` reads as
    ``success={200: Order}`` unless ``__http__`` declares ``success=`` explicitly.
    """

    __slots__ = ()
    __http__: ClassVar[_HttpSpec]


@dataclass(frozen=True, slots=True, kw_only=True)
class _HttpSpec:
    """Everything an HTTP operation declares besides its fields — one record for both forms."""

    method: str
    path: str
    operation_id: str | None = None
    success: SuccessSpec | None = None
    errors: Mapping[Selector, ErrorSpec | Sequence[ErrorSpec]] | Sequence[ErrorSpec] = field(
        default_factory=dict
    )
    fallback: ErrorSpec | None = None
    inherit_errors: bool = True
    security: object = _INHERIT
    requires: tuple[object, ...] = ()
    inject: tuple[Inject, ...] = ()
    signing: object = _INHERIT
    crypto: PayloadCrypto | None | _Inherit = _INHERIT
    protections: tuple[SolverRequirement[Any, Any], ...] = ()
    wire: Wire = EMPTY_WIRE
    projection: BodyProjection[Any, Any] | None = None
    tags: tuple[str, ...] = ()
    idempotent: bool | None = None
    raw_response: bool = False
    discriminator: str | None = None
    """What the service envelope calls this operation; only ``Rpc`` fills it."""

    def __post_init__(self) -> None:
        if _METHOD_TOKEN.fullmatch(self.method) is None:
            raise ValueError(f"invalid HTTP method token: {self.method!r}")
        object.__setattr__(self, "method", self.method.upper())
        if not isinstance(self.path, str):
            raise TypeError("operation path must be a string")


class _HttpOptions(TypedDict, total=False):
    operation_id: str | None
    success: SuccessSpec | None
    errors: Mapping[Selector, ErrorSpec | Sequence[ErrorSpec]] | Sequence[ErrorSpec]
    fallback: ErrorSpec | None
    inherit_errors: bool
    security: object
    requires: tuple[object, ...]
    inject: tuple[Inject, ...]
    signing: object
    crypto: PayloadCrypto | None | _Inherit
    protections: tuple[SolverRequirement[Any, Any], ...]
    wire: Wire
    projection: BodyProjection[Any, Any] | None
    tags: tuple[str, ...]
    idempotent: bool | None
    raw_response: bool


class Http:
    """The public entry for HTTP verbs: ``Http.get(...)``, ``Http.post(...)``, ``Http.request``.

    Assigned to ``__http__`` on an operation class. The decorator ``api.get(...)`` calls the
    same function, so the keyword set is one by construction.
    """

    __slots__ = ()

    @staticmethod
    def request(method: str, path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method=method, path=path, **options)

    @staticmethod
    def delete(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method="DELETE", path=path, **options)

    @staticmethod
    def get(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method="GET", path=path, **options)

    @staticmethod
    def head(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method="HEAD", path=path, **options)

    @staticmethod
    def options(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method="OPTIONS", path=path, **options)

    @staticmethod
    def patch(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method="PATCH", path=path, **options)

    @staticmethod
    def post(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method="POST", path=path, **options)

    @staticmethod
    def put(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method="PUT", path=path, **options)

    @staticmethod
    def trace(path: str, /, **options: Unpack[_HttpOptions]) -> _HttpSpec:
        return _HttpSpec(method="TRACE", path=path, **options)


def generic_argument(operation_type: type[object], base: type[object]) -> object | None:
    """The ``T`` in ``class Op(base[T])``, searched through the class and its bases."""

    for cls in operation_type.__mro__:
        for orig in getattr(cls, "__orig_bases__", ()):
            if get_origin(orig) is base:
                arguments = get_args(orig)
                if arguments and arguments[0] is not Any:
                    return cast(object, arguments[0])
    return None


__all__ = ["Http", "HttpOperation"]
