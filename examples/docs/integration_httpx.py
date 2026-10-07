"""Runnable HTTPX integration example for the documentation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from eazy_sdk import AsyncApi, AsyncClient, Client, Http, HttpOperation, SyncApi, op

from .guide_site import GuideSite, guide_origin


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    subject: str


@dataclass(frozen=True, slots=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/42")


class MessageApi(SyncApi):
    message = op(GetMessage)


class AsyncMessageApi(AsyncApi):
    message = op(GetMessage)


def sync_message(base_url: str) -> Message:
    # docs:integration-httpx-sync:start
    # examples/docs/integration_httpx.py
    with Client.httpx(base_url=base_url) as client:
        return MessageApi(client).message()
    # docs:integration-httpx-sync:end


async def async_message(base_url: str) -> Message:
    # docs:integration-httpx-async:start
    # examples/docs/integration_httpx.py
    async with AsyncClient.httpx(base_url=base_url) as client:
        return await AsyncMessageApi(client).message()
    # docs:integration-httpx-async:end


async def main() -> None:
    site = GuideSite()
    with guide_origin(site) as base_url:
        sync = sync_message(base_url)
        asynchronous = await async_message(base_url)
    print(f"sync: {sync.id} {sync.subject}")
    print(f"async: {asynchronous.id} {asynchronous.subject}")


if __name__ == "__main__":
    asyncio.run(main())
