"""Ports the compiler depends on; implementations live in higher layers."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CryptoProfile(Protocol):
    """Structural view of a payload-crypto profile (implemented by ``crypto.PayloadCrypto``)."""

    @property
    def name(self) -> str: ...

    @property
    def outbound(self) -> Any | None: ...

    @property
    def inbound(self) -> Any | None: ...

    @property
    def inputs(self) -> tuple[Any, ...]: ...
