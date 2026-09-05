"""Public transport-agnostic clients constructed from Zapros handlers."""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from weakref import WeakSet

from zapros import (
    AsyncBaseHandler,
    AsyncStdNetworkHandler,
    BaseHandler,
    Response,
    StdNetworkHandler,
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
    """Synchronous client over one Zapros handler.

    ``Client(base_url=...)`` uses Zapros' standard network handler. ``Client.httpx()``,
    ``Client.requests()`` and ``Client.curl_cffi()`` build the first-party handler for you;
    pass ``handler=`` to bring your own.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        handler: BaseHandler | None = None,
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
    ) -> None:
        if handler is None:
            handler = StdNetworkHandler()
            owns_handler = True
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
            protection_session_owner=handler,
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
            try:
                super().close()
            finally:
                self._runtime.close_protection_session()
                if self.owns_handler:
                    _remember_closed_handler(self.handler)

    def __enter__(self) -> Client:
        super().__enter__()
        return self

    def _run[T](self, call: Any, options: Any) -> Any:
        if self._closed:
            raise RuntimeError("Eazy SDK Client is closed")
        return super()._run(call, options)

    @classmethod
    def httpx(
        cls,
        *,
        base_url: str = "",
        config: ClientConfig | None = None,
        client: Any | None = None,
        profile: HandlerProfile | None = None,
        proxy: str | None = None,
        **httpx_options: Any,
    ) -> Client:
        """Client over an ``httpx.Client`` (created from ``httpx_options`` when not given).

        ``proxy`` configures the created client and becomes part of the transport identity;
        with a caller-supplied ``client`` it is only the declaration of the proxy that client
        already uses (see ``HandlerProfile.proxy``).
        """

        httpx = _require("httpx", "httpx")
        from eazy_sdk.handlers.httpx import HttpxHandler

        owned = client is None
        raw = client if client is not None else httpx.Client(proxy=proxy, **httpx_options)
        return cls(
            base_url=base_url,
            handler=HttpxHandler(raw, owns_client=owned, proxy=proxy),
            config=config,
            profile=profile,
        )

    @classmethod
    def requests(
        cls,
        *,
        base_url: str = "",
        config: ClientConfig | None = None,
        session: Any | None = None,
        profile: HandlerProfile | None = None,
        proxy: str | None = None,
    ) -> Client:
        """Client over a ``requests.Session`` (created when not given).

        ``proxy`` configures the created session and becomes part of the transport identity;
        with a caller-supplied ``session`` it is only the declaration of the proxy that session
        already uses (see ``HandlerProfile.proxy``).
        """

        _require("requests", "requests")
        from eazy_sdk.handlers.requests import RequestsHandler

        return cls(
            base_url=base_url,
            handler=RequestsHandler(session, owns_session=session is None, proxy=proxy),
            config=config,
            profile=profile,
        )

    @classmethod
    def curl_cffi(
        cls,
        *,
        base_url: str = "",
        config: ClientConfig | None = None,
        session: Any | None = None,
        impersonate: Any | None = None,
        profile: HandlerProfile | None = None,
        proxy: str | None = None,
    ) -> Client:
        """Client over a ``curl_cffi`` session with optional browser impersonation.

        ``proxy`` configures the created session and becomes part of the transport identity;
        with a caller-supplied ``session`` it is only the declaration of the proxy that session
        already uses (see ``HandlerProfile.proxy``).
        """

        _require("curl_cffi", "curl-cffi")
        from eazy_sdk.handlers.curl_cffi import CurlCffiZaprosHandler

        return cls(
            base_url=base_url,
            handler=CurlCffiZaprosHandler(
                session,
                impersonate=impersonate,
                owns_session=session is None,
                proxy=proxy,
            ),
            config=config,
            profile=profile,
        )

    def _async_view(self) -> _AsyncClientCore[Response]:
        """Share this client's runtime with generated async auth services."""
        return _AsyncClientCore(
            self._runtime,
            raw=None,
            default_options=self._default_options,
        )


class AsyncClient(_AsyncClientCore[Response]):
    """Asynchronous client over one Zapros async handler.

    ``AsyncClient(base_url=...)`` uses Zapros' standard async network handler;
    ``AsyncClient.httpx()`` and ``AsyncClient.curl_cffi()`` build first-party handlers.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        handler: AsyncBaseHandler | None = None,
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
    ) -> None:
        if handler is None:
            handler = AsyncStdNetworkHandler()
            owns_handler = True
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
            protection_session_owner=handler,
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
            try:
                await super().aclose()
            finally:
                self._runtime.close_protection_session()
                if self.owns_handler:
                    _remember_closed_handler(self.handler)

    async def __aenter__(self) -> AsyncClient:
        await super().__aenter__()
        return self

    async def _run[T](self, call: Any, options: Any) -> Any:
        if self._closed:
            raise RuntimeError("Eazy SDK AsyncClient is closed")
        return await super()._run(call, options)

    @classmethod
    def httpx(
        cls,
        *,
        base_url: str = "",
        config: ClientConfig | None = None,
        client: Any | None = None,
        profile: HandlerProfile | None = None,
        proxy: str | None = None,
        **httpx_options: Any,
    ) -> AsyncClient:
        """Client over an ``httpx.AsyncClient`` (created from ``httpx_options`` when not given).

        ``proxy`` configures the created client and becomes part of the transport identity;
        with a caller-supplied ``client`` it is only the declaration of the proxy that client
        already uses (see ``HandlerProfile.proxy``).
        """

        httpx = _require("httpx", "httpx")
        from eazy_sdk.handlers.httpx import AsyncHttpxHandler

        owned = client is None
        raw = client if client is not None else httpx.AsyncClient(proxy=proxy, **httpx_options)
        return cls(
            base_url=base_url,
            handler=AsyncHttpxHandler(raw, owns_client=owned, proxy=proxy),
            config=config,
            profile=profile,
        )

    @classmethod
    def curl_cffi(
        cls,
        *,
        base_url: str = "",
        config: ClientConfig | None = None,
        session: Any | None = None,
        impersonate: Any | None = None,
        profile: HandlerProfile | None = None,
        proxy: str | None = None,
    ) -> AsyncClient:
        """Client over a ``curl_cffi.AsyncSession`` with optional browser impersonation.

        ``proxy`` configures the created session and becomes part of the transport identity;
        with a caller-supplied ``session`` it is only the declaration of the proxy that session
        already uses (see ``HandlerProfile.proxy``).
        """

        _require("curl_cffi", "curl-cffi")
        from eazy_sdk.handlers.curl_cffi import AsyncCurlCffiZaprosHandler

        return cls(
            base_url=base_url,
            handler=AsyncCurlCffiZaprosHandler(
                session,
                impersonate=impersonate,
                owns_session=session is None,
                proxy=proxy,
            ),
            config=config,
            profile=profile,
        )


def _require(module: str, extra: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"{module} is not installed; install it with `pip install \"eazy-sdk[{extra}]\"`"
        ) from exc

__all__ = ["AsyncClient", "Client"]
