"""Phase 36: hygiene — exception base/suffix, sync runner, middleware contract, client dedupe."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import threading
from typing import Annotated, Any, cast

import pytest
from zapros import BaseHandler, Request, Response

import eazy_sdk
from eazy_sdk import Client, Json, Path, SyncApi, api
from eazy_sdk.clients import AttemptLimitError, EventLoopConflictError, RedirectLimitError
from eazy_sdk.clients._core import _ClientCore
from eazy_sdk.clients.async_client import _AsyncClientCore
from eazy_sdk.clients.sync_client import _SyncClientCore
from eazy_sdk.core.errors import ConfigurationError, EazySdkError, PlanError
from eazy_sdk.core.http_plan import RequestScope
from eazy_sdk.crypto import CryptoConfigurationError
from eazy_sdk.handlers import CapabilityMismatchError, TransportError
from eazy_sdk.middleware import ScopedMiddleware, attempt_middleware, call_middleware
from eazy_sdk.preparation import PreparationIncompleteError
from eazy_sdk.protection import ProtectionConfigurationError
from eazy_sdk.websocket.errors import ProtocolConfigurationError
from eazy_sdk.websocket.middleware import MessageMiddlewareApplication, WsContinue, WsScope


class Handler(BaseHandler):
    def __init__(self) -> None:
        self.loops: list[int] = []

    def handle(self, request: Request) -> Response:
        self.loops.append(id(asyncio.get_running_loop()))
        return Response(
            200, [("Content-Type", "application/json")], content=b'{"name":"Ada"}', request=request
        )

    def close(self) -> None:
        return None


class Users(SyncApi):
    @api.get("/users/{user_id}", response=Json())
    def get(self, *, user_id: Annotated[int, Path()]) -> dict[str, object]:
        raise NotImplementedError


def _public_exceptions() -> list[type[BaseException]]:
    found: list[type[BaseException]] = []
    for module_info in pkgutil.walk_packages(eazy_sdk.__path__, "eazy_sdk."):
        if any(part.startswith("_") for part in module_info.name.split(".")[1:]):
            continue
        module = importlib.import_module(module_info.name)
        for name in getattr(module, "__all__", ()):
            value = getattr(module, name)
            if inspect.isclass(value) and issubclass(value, BaseException):
                found.append(value)
    return sorted(set(found), key=lambda cls: cls.__name__)


def test_every_public_exception_derives_from_eazy_sdk_error_and_uses_the_error_suffix() -> None:
    exceptions = _public_exceptions()
    assert exceptions
    for cls in exceptions:
        assert issubclass(cls, EazySdkError), cls
        assert cls.__name__.endswith("Error"), cls
    assert not hasattr(eazy_sdk.exceptions, "EazySDKError")
    assert TransportError.__name__ == "TransportError" and issubclass(TransportError, EazySdkError)
    for cls in (AttemptLimitError, RedirectLimitError, CapabilityMismatchError):
        assert issubclass(cls, EazySdkError)
    assert issubclass(PreparationIncompleteError, EazySdkError)


def test_configuration_errors_share_one_base() -> None:
    for cls in (
        ProtectionConfigurationError,
        CryptoConfigurationError,
        ProtocolConfigurationError,
    ):
        assert issubclass(cls, ConfigurationError), cls
        assert issubclass(cls, EazySdkError)
    from eazy_sdk.auth import SessionConfigurationError
    from eazy_sdk.auth.session import SessionConfigurationError as SessionError

    assert SessionConfigurationError is SessionError
    assert issubclass(SessionConfigurationError, ConfigurationError)
    assert issubclass(SessionConfigurationError, PlanError)
    accounts = pytest.importorskip("eazy_sdk_accounts")
    assert issubclass(accounts.RegistrationConfigurationError, ConfigurationError)


def test_sync_client_reuses_one_loop_per_thread_and_rejects_running_loops() -> None:
    handler = Handler()
    with Client(base_url="https://api.test", handler=handler) as client:
        users = Users(client)
        users.get(user_id=1)
        users.get(user_id=2)
        assert len(set(handler.loops)) == 1
        main_loop = handler.loops[0]

        def worker() -> None:
            users.get(user_id=3)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert len(handler.loops) == 3 and handler.loops[2] != main_loop

        async def inside_loop() -> None:
            with pytest.raises(EventLoopConflictError, match="AsyncClient"):
                users.get(user_id=4)

        asyncio.run(inside_loop())
    with pytest.raises(RuntimeError, match="closed"):
        users.get(user_id=5)


def test_one_scoped_middleware_contract_covers_http_and_websocket() -> None:
    class Middleware:
        async def __call__(self, context: object, next_call: object) -> object:
            raise NotImplementedError

    http_call = call_middleware(
        cast(Any, Middleware()), scope=RequestScope(hosts=frozenset({"a.test"}))
    )
    http_attempt = attempt_middleware(object())
    ws_message = MessageMiddlewareApplication(
        implementation=lambda context: WsContinue(), scope=WsScope()
    )

    for entry in (http_call, http_attempt, ws_message):
        assert isinstance(entry, ScopedMiddleware)
        assert entry.scope is not None and entry.implementation is not None
    assert not hasattr(ws_message, "middleware")


def test_storage_hooks_are_observers_not_middleware() -> None:
    storage = pytest.importorskip("eazy_sdk_accounts.storage")
    observers = (
        "StorageObserver",
        "BaseStorageObserver",
        "LoggingObserver",
        "EventRecordingObserver",
    )
    for name in observers:
        assert hasattr(storage, name), name
    for removed in observers:
        assert not hasattr(storage, removed.replace("Observer", "Middleware")), removed
    assert "observers" in inspect.signature(storage.MemoryStorage).parameters


def test_sync_and_async_clients_share_one_core() -> None:
    assert issubclass(_SyncClientCore, _ClientCore)
    assert issubclass(_AsyncClientCore, _ClientCore)
    shared = {"bind_sdk", "invalidate_protection", "_prepare_options"}
    for name in shared:
        assert getattr(_SyncClientCore, name) is getattr(_ClientCore, name), name
        assert getattr(_AsyncClientCore, name) is getattr(_ClientCore, name), name
    assert "_scoped" in _SyncClientCore.__dict__ and "_scoped" in _AsyncClientCore.__dict__
