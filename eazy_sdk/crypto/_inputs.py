"""Resolution of typed crypto inputs through the existing dependency registry."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from typing import Any, cast

from eazy_sdk.core.errors import PlanError
from eazy_sdk.dependencies import DependencyContext, DependencyRegistry, ProviderUnavailable

from .core import CryptoInput, CryptoValues, FrozenValue, freeze_value


async def resolve_crypto_inputs(
    inputs: tuple[CryptoInput[Any], ...],
    registry: DependencyRegistry,
    *,
    operation_id: str,
    attempt: int,
    cache: dict[int, object] | None = None,
) -> tuple[CryptoValues, tuple[tuple[str, FrozenValue], ...]]:
    resolved_dependencies: dict[object, object] = {}
    values: list[tuple[object, object]] = []
    aad: list[tuple[str, FrozenValue]] = []
    for descriptor in inputs:
        identity = id(descriptor)
        if cache is not None and identity in cache:
            value = cache[identity]
        else:
            dependency = descriptor.dependency
            provider = registry.provider(dependency)
            if provider is None:
                raise PlanError(f"missing provider: {dependency.diagnostic_name}")
            method = getattr(provider, "resolve", None)
            if method is None:
                raise PlanError(f"provider has no resolve method: {dependency.diagnostic_name}")
            value = method(
                DependencyContext(
                    operation_id,
                    attempt,
                    cast(dict[Any, object], resolved_dependencies),
                )
            )
            if inspect.isawaitable(value):
                value = await cast(Awaitable[object], value)
            if isinstance(value, ProviderUnavailable):
                raise PlanError(f"required dependency unavailable: {dependency.diagnostic_name}")
            value = dependency.validator(value)
            if cache is not None:
                cache[identity] = value
        resolved_dependencies[descriptor.dependency] = value
        values.append((descriptor, value))
        if descriptor.aad:
            aad.append((descriptor.name, freeze_value(value)))
    return CryptoValues(tuple(values)), tuple(aad)


__all__: list[str] = []
