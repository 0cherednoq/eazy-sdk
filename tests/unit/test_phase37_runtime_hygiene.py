"""Phase 37: runtime hygiene — sync runner leaves the thread's loop alone, lock backoff cap."""

from __future__ import annotations

import asyncio
import threading
from typing import Annotated, cast

from zapros import BaseHandler, Request, Response

from eazy_sdk import Client, Json, SyncApi, api
from eazy_sdk.auth import session_auth, session_scheme
from eazy_sdk.auth.session_runtime import generated_session_auth, generated_session_scheme
from eazy_sdk.clients import executor
from eazy_sdk.codegen import generated_session_auth as codegen_session_auth
from eazy_sdk.request.markers import Path


class Handler(BaseHandler):
    def handle(self, request: Request) -> Response:
        return Response(
            200, [("Content-Type", "application/json")], content=b'{"name":"Ada"}', request=request
        )

    def close(self) -> None:
        return None


class Users(SyncApi):
    @api.get("/users/{user_id}", response=Json())
    def get(self, *, user_id: Annotated[int, Path()]) -> dict[str, object]:
        raise NotImplementedError


def test_sync_client_keeps_the_loop_the_caller_installed() -> None:
    own_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(own_loop)
    try:
        with Client(base_url="https://api.test", handler=Handler()) as client:
            users = Users(client)
            assert users.get(user_id=1) == {"name": "Ada"}
            assert asyncio.get_event_loop() is own_loop
            assert users.get(user_id=2) == {"name": "Ada"}
        assert asyncio.get_event_loop() is own_loop
        assert not own_loop.is_closed()
    finally:
        asyncio.set_event_loop(None)
        own_loop.close()


def test_a_synchronous_call_installs_no_loop_in_a_fresh_thread() -> None:
    seen: dict[str, object] = {}

    def worker() -> None:
        with Client(base_url="https://api.test", handler=Handler()) as client:
            seen["result"] = Users(client).get(user_id=1)
        try:
            # Raises in a non-main thread unless a loop was installed there.
            seen["current"] = asyncio.get_event_loop()
        except RuntimeError:
            seen["current"] = None

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert seen["result"] == {"name": "Ada"}
    assert seen["current"] is None


def test_protection_lock_backoff_is_capped_at_ten_milliseconds() -> None:
    assert executor._MAX_POLL_DELAY == 0.01


def test_generated_session_helpers_are_public_and_distinct_from_the_authoring_ones() -> None:
    assert codegen_session_auth is generated_session_auth
    assert cast(object, generated_session_auth) is not cast(object, session_auth)
    assert cast(object, generated_session_scheme) is not cast(object, session_scheme)
    assert not generated_session_auth.__name__.startswith("_")
    assert not generated_session_scheme.__name__.startswith("_")
