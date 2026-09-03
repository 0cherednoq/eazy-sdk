"""Private operation declarations and calls produced by public API descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from eazy_sdk.auth import AuthScheme, SecurityAlternative, SecurityPolicy
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.core.kernel import BoundArguments
from eazy_sdk.core.ports import CryptoProfile
from eazy_sdk.protection.advanced import SolverRequirement
from eazy_sdk.request import BodyProjection, WireOptions
from eazy_sdk.request.signatures import RequestSignature
from eazy_sdk.response import Responses

from .http_compiler import CompiledContract, HttpCompilerRegistry, compile_endpoint
from .input import InputField, MethodInputSchema


@dataclass(frozen=True, slots=True)
class _OperationCall[T]:
    declaration: _OperationDeclaration[T]
    arguments: BoundArguments


@dataclass(frozen=True, slots=True)
class _OperationDeclaration[T]:
    operation_id: str
    method: str
    path: str
    input_fields: tuple[InputField, ...]
    input_schema: MethodInputSchema
    result_type: object
    responses: Responses[T] | object
    security: AuthScheme[Any] | SecurityAlternative | SecurityPolicy | None = None
    requires: tuple[object, ...] = ()
    inject: tuple[object, ...] = ()
    signing: tuple[RequestSignature, ...] = ()
    protections: tuple[SolverRequirement[Any, Any], ...] = ()
    body_projection: BodyProjection[Any, Any] | None = None
    wire: WireOptions | None = None
    scope: RequestScope = field(default_factory=RequestScope)
    tags: tuple[str, ...] = ()
    idempotent: bool | None = None
    raw_response: bool = False
    crypto: CryptoProfile | None = None
    crypto_wire: object | None = None
    crypto_inherit: bool = True

    @property
    def declaration(self) -> _OperationDeclaration[T]:
        """A declaration references itself, so it satisfies ``OperationReference``."""

        return self

    def __post_init__(self) -> None:
        if not self.operation_id:
            raise ValueError("operation_id must not be empty")
        if not self.method:
            raise ValueError("method must not be empty")
        object.__setattr__(self, "method", self.method.upper())
        if isinstance(self.security, AuthScheme):
            object.__setattr__(
                self,
                "security",
                SecurityPolicy((SecurityAlternative((self.security,)),)),
            )
        elif isinstance(self.security, SecurityAlternative):
            object.__setattr__(self, "security", SecurityPolicy((self.security,)))

    @property
    def is_idempotent(self) -> bool:
        if self.idempotent is not None:
            return self.idempotent
        return self.method in {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}

    def compile(self, *, registry: HttpCompilerRegistry | None = None) -> CompiledContract[T]:
        return compile_endpoint(self, registry=registry, scope=self.scope)

    def call(self, values: dict[str, object], /) -> _OperationCall[T]:
        compiled = self.compile()
        arguments = compiled.bind_input(values)
        return _OperationCall(self, arguments)

    def at_path(self, absolute_url: str) -> _OperationDeclaration[T]:
        return replace(self, path=absolute_url)


__all__: list[str] = []
