"""Typed identity-based request dependencies for the compiled runtime."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from typing import Any, Protocol, cast

from eazy_sdk.core.errors import GraphError, PlanError
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.core.kernel import (
    Append,
    PatchConflict,
    PythonTypeValidator,
    ReplaceAll,
    Set,
    ValuePatch,
    ValueSlot,
    ValueValidator,
)
from eazy_sdk.request.descriptors import Form, JsonField, Part
from eazy_sdk.request.params import Cookie, Header, Path, Query


class DependencyCachePolicy(Enum):
    NONE = "none"
    CLIENT = "client"
    CALL = "call"
    ATTEMPT = "attempt"


@dataclass(frozen=True, slots=True, eq=False)
class RequestDependency[T]:
    diagnostic_name: str
    validator: ValueValidator[T]
    cache: DependencyCachePolicy = DependencyCachePolicy.CALL
    secret: bool = False

    @classmethod
    def typed(
        cls,
        diagnostic_name: str,
        annotation: object,
        *,
        cache: DependencyCachePolicy = DependencyCachePolicy.CALL,
        secret: bool = False,
    ) -> RequestDependency[Any]:
        return cls(diagnostic_name, PythonTypeValidator(annotation), cache, secret)


class ResultSelector[TSource, TValue](Protocol):
    def select(self, value: TSource) -> TValue: ...


@dataclass(frozen=True, slots=True)
class _IdentitySelector[T]:
    def select(self, value: T) -> T:
        return value


@dataclass(frozen=True, slots=True)
class _AttributeSelector[TSource, TValue]:
    name: str

    def select(self, value: TSource) -> TValue:
        try:
            return cast(TValue, getattr(value, self.name))
        except AttributeError as exc:
            raise PlanError(f"dependency result has no attribute {self.name!r}") from exc


@dataclass(frozen=True, slots=True)
class _MappingSelector[TValue]:
    key: str

    def select(self, value: Mapping[str, object]) -> TValue:
        try:
            return cast(TValue, value[self.key])
        except KeyError as exc:
            raise PlanError(f"dependency result has no key {self.key!r}") from exc


class _BindingOperation(Enum):
    SET = "set"
    APPEND = "append"
    REPLACE_ALL = "replace-all"


@dataclass(frozen=True, slots=True)
class _LogicalBinding:
    selector: ResultSelector[Any, Any]
    location: str
    name: str
    operation: _BindingOperation = _BindingOperation.SET


@dataclass(frozen=True, slots=True)
class FieldBinding:
    selector: ResultSelector[Any, Any]

    def to_header(self, name: str) -> _LogicalBinding:
        return _LogicalBinding(self.selector, "header", name)

    def to_query(self, name: str) -> _LogicalBinding:
        return _LogicalBinding(self.selector, "query", name)

    def to_cookie(self, name: str) -> _LogicalBinding:
        return _LogicalBinding(self.selector, "cookie", name)

    def to_body(self, name: str) -> _LogicalBinding:
        return _LogicalBinding(self.selector, "body", name)


@dataclass(frozen=True, slots=True)
class DependencySpec[T]:
    dependency: RequestDependency[T]
    provider: object | None
    bindings: tuple[_LogicalBinding, ...]
    required: bool = True
    requires: tuple[RequestDependency[Any], ...] = ()


@dataclass(frozen=True, slots=True)
class Inject:
    placement: Path | Query | Header | Cookie | JsonField | Form | Part
    source: object
    cache: DependencyCachePolicy = DependencyCachePolicy.ATTEMPT
    secret: bool = False

    @property
    def wire_name(self) -> str:
        name = self.placement.name
        if not isinstance(name, str) or not name:
            raise PlanError("Inject placement requires an explicit wire name")
        return name

    @property
    def location(self) -> str:
        if isinstance(self.placement, Path):
            return "path"
        if isinstance(self.placement, Query):
            return "query"
        if isinstance(self.placement, Header):
            return "header"
        if isinstance(self.placement, Cookie):
            return "cookie"
        return "body"


@dataclass(frozen=True, slots=True)
class _ResultBinding[TSource, TValue]:
    select: ResultSelector[TSource, TValue]
    target: ValueSlot[TValue]
    operation: _BindingOperation = _BindingOperation.SET
    conflict: PatchConflict = PatchConflict.ERROR

    def operation_for(self, value: TSource) -> Set[TValue] | Append[TValue] | ReplaceAll[TValue]:
        selected = self.select.select(value)
        if self.operation is _BindingOperation.APPEND:
            return Append(self.target, selected, self.conflict)
        if self.operation is _BindingOperation.REPLACE_ALL:
            if not isinstance(selected, tuple | list):
                raise PlanError("replace-all dependency binding requires a sequence")
            return ReplaceAll(self.target, selected, self.conflict)
        return Set(self.target, selected, self.conflict)


@dataclass(frozen=True, slots=True)
class _RequestRequirement[T]:
    dependency: RequestDependency[T]
    required: bool
    bindings: tuple[_ResultBinding[T, Any], ...]
    scope: RequestScope = dataclass_field(default_factory=RequestScope)
    requires: tuple[RequestDependency[Any], ...] = ()


@dataclass(frozen=True, slots=True)
class DependencyContext:
    operation_id: str
    attempt: int
    resolved: Mapping[RequestDependency[Any], object]


class DependencyProvider[T](Protocol):
    def resolve(self, context: DependencyContext) -> T: ...


class AsyncDependencyProvider[T](Protocol):
    async def resolve(self, context: DependencyContext) -> T: ...


@dataclass(frozen=True, slots=True)
class ProviderUnavailable:
    reason: str = "unavailable"


class DependencyRegistry:
    def __init__(self) -> None:
        self._providers: dict[int, object] = {}

    def register[T](self, dependency: RequestDependency[T], provider: object) -> None:
        if id(dependency) in self._providers:
            raise PlanError(f"dependency already registered: {dependency.diagnostic_name}")
        self._providers[id(dependency)] = provider

    def provider[T](self, dependency: RequestDependency[T]) -> object | None:
        return self._providers.get(id(dependency))

    def ensure[T](self, dependency: RequestDependency[T], provider: object) -> None:
        self._providers.setdefault(id(dependency), provider)


@dataclass(slots=True)
class _DependencyCaches:
    client: dict[int, object] = dataclass_field(default_factory=dict)
    call: dict[int, object] = dataclass_field(default_factory=dict)
    attempt: dict[int, object] = dataclass_field(default_factory=dict)

    def cache_for(self, policy: DependencyCachePolicy) -> dict[int, object] | None:
        return {
            DependencyCachePolicy.CLIENT: self.client,
            DependencyCachePolicy.CALL: self.call,
            DependencyCachePolicy.ATTEMPT: self.attempt,
        }.get(policy)


def _compile_dependency_order(
    requirements: tuple[_RequestRequirement[Any], ...],
) -> tuple[_RequestRequirement[Any], ...]:
    by_dependency = {id(item.dependency): item for item in requirements}
    edges: dict[int, set[int]] = {id(item.dependency): set() for item in requirements}
    indegree = {id(item.dependency): 0 for item in requirements}
    for requirement in requirements:
        for dependency in requirement.requires:
            source = by_dependency.get(id(dependency))
            if source is None:
                raise GraphError(
                    f"dependency {requirement.dependency.diagnostic_name!r} requires "
                    f"unregistered {dependency.diagnostic_name!r}"
                )
            edges[id(dependency)].add(id(requirement.dependency))
            indegree[id(requirement.dependency)] += 1
    ready = sorted(
        (item for item in requirements if indegree[id(item.dependency)] == 0),
        key=lambda item: item.dependency.diagnostic_name,
    )
    output: list[_RequestRequirement[Any]] = []
    while ready:
        item = ready.pop(0)
        output.append(item)
        for target in edges[id(item.dependency)]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(by_dependency[target])
    if len(output) != len(requirements):
        names = [
            item.dependency.diagnostic_name
            for item in requirements
            if indegree[id(item.dependency)]
        ]
        raise GraphError("dependency cycle: " + " -> ".join((*names, names[0])))
    return tuple(output)


async def _resolve_requirements(
    requirements: tuple[_RequestRequirement[Any], ...],
    registry: DependencyRegistry,
    *,
    operation_id: str,
    attempt: int,
    caches: _DependencyCaches | None = None,
) -> ValuePatch:
    caches = caches or _DependencyCaches()
    resolved: dict[RequestDependency[Any], object] = {}
    operations: list[Any] = []
    for requirement in _compile_dependency_order(requirements):
        dependency = requirement.dependency
        cache = caches.cache_for(dependency.cache)
        if cache is not None and id(dependency) in cache:
            value = cache[id(dependency)]
        else:
            provider = registry.provider(dependency)
            if provider is None:
                if requirement.required:
                    raise PlanError(f"missing provider: {dependency.diagnostic_name}")
                continue
            method = getattr(provider, "resolve", None)
            if method is None:
                raise PlanError(f"provider has no resolve method: {dependency.diagnostic_name}")
            value = method(DependencyContext(operation_id, attempt, resolved))
            if inspect.isawaitable(value):
                value = await cast(Awaitable[object], value)
            if isinstance(value, ProviderUnavailable):
                if requirement.required:
                    raise PlanError(
                        f"required dependency unavailable: {dependency.diagnostic_name}"
                    )
                continue
            value = dependency.validator(value)
            if cache is not None:
                cache[id(dependency)] = value
        resolved[dependency] = value
        operations.extend(binding.operation_for(value) for binding in requirement.bindings)
    return ValuePatch(tuple(operations))


def dependency[T](
    annotation: type[T],
    *,
    name: str | None = None,
    provide: object | None = None,
    apply: tuple[_LogicalBinding, ...] = (),
    cache: DependencyCachePolicy = DependencyCachePolicy.CALL,
    secret: bool = False,
) -> DependencySpec[T]:
    descriptor = cast(
        RequestDependency[T],
        RequestDependency.typed(
            name or annotation.__name__, annotation, cache=cache, secret=secret
        ),
    )
    return DependencySpec(descriptor, provide, apply)


def field(name: str) -> FieldBinding:
    return FieldBinding(_AttributeSelector(name))


def value() -> FieldBinding:
    return FieldBinding(_IdentitySelector())


def _lower_requirements(
    requirements: tuple[object, ...], compiled: object, registry: DependencyRegistry
) -> tuple[_RequestRequirement[Any], ...]:
    output: list[_RequestRequirement[Any]] = []
    for item in requirements:
        if isinstance(item, Inject):
            descriptor = cast(
                RequestDependency[object],
                RequestDependency.typed(
                    f"inject.{item.location}.{item.wire_name}",
                    object,
                    cache=item.cache,
                    secret=item.secret,
                ),
            )
            provider: object
            if callable(item.source) or hasattr(item.source, "resolve"):
                provider = item.source
            else:
                provider = _ConstantProvider(item.source)
            if callable(provider) and not hasattr(provider, "resolve"):
                provider = _CallableProvider(provider)
            registry.ensure(descriptor, provider)
            slots = getattr(compiled, f"{item.location}_slots", None)
            if item.location == "body":
                slots = getattr(compiled, "body_field_slots", {})
            slot = slots.get(item.wire_name) if slots is not None else None
            if slot is None:
                raise PlanError(
                    f"injected target was not compiled: {item.location}.{item.wire_name}"
                )
            output.append(
                _RequestRequirement(
                    descriptor,
                    True,
                    (_ResultBinding(_IdentitySelector(), slot),),
                )
            )
            continue
        if isinstance(item, _RequestRequirement):
            output.append(item)
            continue
        if not isinstance(item, DependencySpec):
            raise PlanError(f"unsupported dependency requirement: {type(item).__name__}")
        if item.provider is not None:
            provider = item.provider
            if callable(provider) and not hasattr(provider, "resolve"):
                provider = _CallableProvider(provider)
            registry.ensure(item.dependency, provider)
        bindings: list[_ResultBinding[Any, Any]] = []
        for binding in item.bindings:
            slots = getattr(compiled, f"{binding.location}_slots", None)
            if binding.location == "body":
                body_fields = getattr(compiled, "body_field_slots", {})
                slot = body_fields.get(binding.name) or getattr(compiled, "body_slot", None)
            else:
                slot = slots.get(binding.name) if slots is not None else None
            if slot is None:
                raise PlanError(
                    f"dependency target is not declared: {binding.location}.{binding.name}"
                )
            bindings.append(_ResultBinding(binding.selector, slot, binding.operation))
        output.append(
            _RequestRequirement(
                item.dependency, item.required, tuple(bindings), requires=item.requires
            )
        )
    return tuple(output)


@dataclass(frozen=True, slots=True)
class _CallableProvider:
    callback: object

    def resolve(self, context: DependencyContext) -> object:
        if not callable(self.callback):
            raise TypeError("dependency provider must be callable")
        signature = inspect.signature(self.callback)
        return self.callback() if not signature.parameters else self.callback(context)


@dataclass(frozen=True, slots=True)
class _ConstantProvider:
    value: object

    def resolve(self, context: DependencyContext) -> object:
        return self.value


__all__ = [
    "AsyncDependencyProvider",
    "DependencyCachePolicy",
    "DependencyContext",
    "DependencyProvider",
    "DependencyRegistry",
    "DependencySpec",
    "Inject",
    "ProviderUnavailable",
    "RequestDependency",
    "dependency",
    "field",
    "value",
]
