"""Public transport-agnostic clients constructed from Zapros handlers."""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from weakref import WeakSet

from zapros import (
    AsyncBaseHandler,
    BaseHandler,
    Response,
)
from zapros import (
    AsyncClient as RawAsyncClient,
)
from zapros import (
    Client as RawClient,
)

from eazy_sdk.handlers import (
    CONSERVATIVE_HANDLER_PROFILE,
    BorrowedAsyncHandler,
    BorrowedHandler,
    HandlerProfile,
    ZaprosAsyncEmitter,
    ZaprosSyncEmitter,
)
from eazy_sdk.protection import NetworkIdentity, NetworkIdentityProvider

from .async_client import _AsyncClientCore
from .config import ClientConfig, _runtime_from_boundary
from .sync_client import _SyncClientCore

_CLOSED_OWNED_HANDLERS: WeakSet[object] = WeakSet()


def _reject_closed_handler(handler: object) -> None:
    try:
        closed = handler in _CLOSED_OWNED_HANDLERS
    except TypeError:
        closed = False
    if closed:
        raise RuntimeError("Zapros handler was already closed by a Eazy SDK client")


def _remember_closed_handler(handler: object) -> None:
    # A third-party extension may deliberately disable weak references. Its own
    # post-close diagnostics remain authoritative; Eazy SDK never recreates it.
    with suppress(TypeError):
        _CLOSED_OWNED_HANDLERS.add(handler)


class Client(_SyncClientCore[Response]):
    def __init__(
        self,
        *,
        base_url: str = "",
        handler: BaseHandler,
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
    ) -> None:
        if not isinstance(handler, BaseHandler):
            raise TypeError("Client requires a synchronous Zapros BaseHandler")
        _reject_closed_handler(handler)
        selected = config or ClientConfig()
        declared_profile = getattr(handler, "profile", None)
        selected_profile = profile or (
            declared_profile
            if isinstance(declared_profile, HandlerProfile)
            else CONSERVATIVE_HANDLER_PROFILE
        )
        boundary = handler if owns_handler else BorrowedHandler(handler)
        raw = RawClient(handler=boundary, base_url=base_url or None)
        runtime = _runtime_from_boundary(
            selected_profile,
            ZaprosSyncEmitter(raw),
            base_url=base_url,
            config=selected,
            allow_async_crypto=False,
            network_identity=_select_network_identity(selected, handler),
        )
        super().__init__(
            runtime,
            raw=raw,
            default_options=selected.call_options(),
        )
        self.handler = handler
        self.owns_handler = owns_handler
        self.profile = selected_profile
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            super().close()
            if self.owns_handler:
                _remember_closed_handler(self.handler)

    def __enter__(self) -> Client:
        super().__enter__()
        return self

    def _run[T](self, call: Any, options: Any) -> Any:
        if self._closed:
            raise RuntimeError("Eazy SDK Client is closed")
        return super()._run(call, options)

    def _async_view(self) -> _AsyncClientCore[Response]:
        """Share this client's runtime with generated async auth services."""
        return _AsyncClientCore(
            self._runtime,
            raw=None,
            default_options=self._default_options,
        )


class AsyncClient(_AsyncClientCore[Response]):
    def __init__(
        self,
        *,
        base_url: str = "",
        handler: AsyncBaseHandler,
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
    ) -> None:
        if not isinstance(handler, AsyncBaseHandler):
            raise TypeError("AsyncClient requires an asynchronous Zapros AsyncBaseHandler")
        _reject_closed_handler(handler)
        selected = config or ClientConfig()
        declared_profile = getattr(handler, "profile", None)
        selected_profile = profile or (
            declared_profile
            if isinstance(declared_profile, HandlerProfile)
            else CONSERVATIVE_HANDLER_PROFILE
        )
        boundary = handler if owns_handler else BorrowedAsyncHandler(handler)
        raw = RawAsyncClient(handler=boundary, base_url=base_url or None)
        runtime = _runtime_from_boundary(
            selected_profile,
            ZaprosAsyncEmitter(raw),
            base_url=base_url,
            config=selected,
            allow_async_crypto=True,
            network_identity=_select_network_identity(selected, handler),
        )
        super().__init__(
            runtime,
            raw=raw,
            default_options=selected.call_options(),
        )
        self.handler = handler
        self.owns_handler = owns_handler
        self.profile = selected_profile
        self._closed = False

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await super().aclose()
            if self.owns_handler:
                _remember_closed_handler(self.handler)

    async def __aenter__(self) -> AsyncClient:
        await super().__aenter__()
        return self

    async def _run[T](self, call: Any, options: Any) -> Any:
        if self._closed:
            raise RuntimeError("Eazy SDK AsyncClient is closed")
        return await super()._run(call, options)


def _select_network_identity(
    config: ClientConfig,
    handler: object,
) -> NetworkIdentity | NetworkIdentityProvider | None:
    configured = config.network_identity
    declared = getattr(handler, "network_identity", None)
    if declared is not None and not isinstance(declared, NetworkIdentity | NetworkIdentityProvider):
        raise TypeError(
            "handler network_identity must be NetworkIdentity or NetworkIdentityProvider"
        )
    if configured is not None and declared is not None:
        same_source = configured is declared or (
            isinstance(configured, NetworkIdentity)
            and isinstance(declared, NetworkIdentity)
            and configured == declared
        )
        if not same_source:
            raise ValueError("client config and handler declare conflicting network identities")
    return configured if configured is not None else declared


__all__ = ["AsyncClient", "Client"]
