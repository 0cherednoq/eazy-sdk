"""Distinct WebSocket upgrade, protocol and per-message authentication applications."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .protection import SecretText

type CredentialProvider = Callable[[], object | Awaitable[object]]


class StaticUpgradeAuth:
    """Immutable headers applied once when the Zapros client is constructed."""

    __slots__ = ("_headers",)

    def __init__(self, headers: Mapping[str, str | SecretText]) -> None:
        normalized: list[tuple[str, SecretText]] = []
        seen: set[str] = set()
        for name, value in headers.items():
            normalized_name = name.strip().lower()
            if not normalized_name:
                raise ValueError("upgrade authentication header name cannot be empty")
            if normalized_name in seen:
                raise ValueError(f"duplicate upgrade authentication header: {normalized_name}")
            seen.add(normalized_name)
            normalized.append(
                (
                    normalized_name,
                    value if isinstance(value, SecretText) else SecretText(value),
                )
            )
        self._headers = tuple(normalized)

    def headers(self) -> Mapping[str, str]:
        return MappingProxyType({name: value.reveal() for name, value in self._headers})

    def __repr__(self) -> str:
        names = tuple(name for name, _ in self._headers)
        return f"StaticUpgradeAuth(headers={names!r}, values='[REDACTED]')"


@dataclass(frozen=True, slots=True)
class ProtocolAuth:
    """A protocol message whose credential is refreshed for every connection."""

    discriminator: str
    provider: CredentialProvider = field(repr=False)
    await_ready: bool = False
    ready_timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.discriminator:
            raise ValueError("protocol authentication discriminator cannot be empty")
        if self.ready_timeout <= 0:
            raise ValueError("protocol authentication ready_timeout must be positive")

    async def resolve(self) -> object:
        return await resolve_credential(self.provider)


@dataclass(frozen=True, slots=True)
class DynamicPerMessageAuth:
    """A semantic output refreshed before every outbound message attempt."""

    output_path: tuple[str, ...]
    provider: CredentialProvider = field(repr=False)
    name: str = "dynamic-per-message-auth"

    def __post_init__(self) -> None:
        if not self.output_path or not all(self.output_path):
            raise ValueError("dynamic per-message authentication output path is invalid")

    async def resolve(self) -> object:
        value = await resolve_credential(self.provider)
        return value.reveal() if isinstance(value, SecretText) else value


async def resolve_credential(provider: CredentialProvider) -> object:
    value = provider()
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "CredentialProvider",
    "DynamicPerMessageAuth",
    "ProtocolAuth",
    "StaticUpgradeAuth",
]
