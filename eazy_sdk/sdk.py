"""Optional root SDK facades over the public Eazy SDK clients."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast, overload

from zapros import AsyncBaseHandler, BaseHandler

from eazy_sdk.api import AsyncApi, SyncApi
from eazy_sdk.clients import ClientConfig
from eazy_sdk.handlers import HandlerProfile

TRoot = TypeVar("TRoot", bound="SyncSdk")
TAsyncRoot = TypeVar("TAsyncRoot", bound="AsyncSdk")


class _SyncClient(Protocol):
    def bind_sdk[T](self, factory: Callable[[Any], T]) -> T: ...

    def close(self) -> None: ...


class _AsyncClient(Protocol):
    def bind_sdk[T](self, factory: Callable[[Any], T]) -> T: ...

    async def aclose(self) -> None: ...


class _ApiGroup[TGroup: SyncApi | AsyncApi]:
    def __init__(self, api_type: type[TGroup]) -> None:
        self.api_type = api_type
        self.name = ""

    def __set_name__(self, owner: type[object], name: str) -> None:
        self.name = name

    @overload
    def __get__(self, instance: None, owner: type[object]) -> _ApiGroup[TGroup]: ...

    @overload
    def __get__(
        self, instance: SyncSdk | AsyncSdk, owner: type[object] | None = None
    ) -> TGroup: ...

    def __get__(
        self,
        instance: SyncSdk | AsyncSdk | None,
        owner: type[object] | None = None,
    ) -> _ApiGroup[TGroup] | TGroup:
        if instance is None:
            return self
        cached = instance.__dict__.get(self.name)
        if cached is None:
            cached = self.api_type(cast(Any, instance._client))
            instance.__dict__[self.name] = cached
        return cast(TGroup, cached)


def api_group[TGroupApi: SyncApi | AsyncApi](
    api_type: type[TGroupApi],
) -> _ApiGroup[TGroupApi]:
    """Declare a lazily bound API group on a root SDK class."""

    return _ApiGroup(api_type)


class SyncSdk:
    """Root facade with explicit ownership of a synchronous client."""

    def __init__(self, client: _SyncClient, *, owns_client: bool = False) -> None:
        self._client = client
        self._owns_client = owns_client
        self._closed = False

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _validate_groups(cls, asynchronous=False)

    @classmethod
    def from_client(
        cls: type[TRoot],
        client: _SyncClient,
        *,
        owns_client: bool = False,
    ) -> TRoot:
        root = client.bind_sdk(lambda scoped: cls(scoped, owns_client=False))
        root._owns_client = owns_client
        return root

    @classmethod
    def from_handler(
        cls: type[TRoot],
        *,
        handler: BaseHandler,
        base_url: str = "",
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
    ) -> TRoot:
        from eazy_sdk.clients import Client

        client = Client(
            base_url=base_url,
            handler=handler,
            config=config,
            owns_handler=owns_handler,
            profile=profile,
        )
        return cls.from_client(client, owns_client=True)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_client:
                self._client.close()

    def __enter__(self: TRoot) -> TRoot:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class AsyncSdk:
    """Root facade with explicit ownership of an asynchronous client."""

    def __init__(self, client: _AsyncClient, *, owns_client: bool = False) -> None:
        self._client = client
        self._owns_client = owns_client
        self._closed = False

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        _validate_groups(cls, asynchronous=True)

    @classmethod
    def from_client(
        cls: type[TAsyncRoot],
        client: _AsyncClient,
        *,
        owns_client: bool = False,
    ) -> TAsyncRoot:
        root = client.bind_sdk(lambda scoped: cls(scoped, owns_client=False))
        root._owns_client = owns_client
        return root

    @classmethod
    def from_handler(
        cls: type[TAsyncRoot],
        *,
        handler: AsyncBaseHandler,
        base_url: str = "",
        config: ClientConfig | None = None,
        owns_handler: bool = True,
        profile: HandlerProfile | None = None,
    ) -> TAsyncRoot:
        from eazy_sdk.clients import AsyncClient

        client = AsyncClient(
            base_url=base_url,
            handler=handler,
            config=config,
            owns_handler=owns_handler,
            profile=profile,
        )
        return cls.from_client(client, owns_client=True)

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_client:
                await self._client.aclose()

    async def __aenter__(self: TAsyncRoot) -> TAsyncRoot:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


def _validate_groups(root: type[object], *, asynchronous: bool) -> None:
    expected = AsyncApi if asynchronous else SyncApi
    for name, value in root.__dict__.items():
        if isinstance(value, _ApiGroup) and not issubclass(value.api_type, expected):
            kind = "async" if asynchronous else "sync"
            raise TypeError(f"{kind} SDK group {name!r} uses the wrong API kind")


__all__ = ["AsyncSdk", "SyncSdk", "api_group"]
