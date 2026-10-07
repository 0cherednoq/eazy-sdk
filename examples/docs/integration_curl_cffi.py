"""Runnable curl_cffi integration example for the documentation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from eazy_sdk import AsyncApi, AsyncClient, Http, HttpOperation, op

from .guide_site import GuideSite, guide_origin


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    subject: str


@dataclass(frozen=True, slots=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/42")


class MessageApi(AsyncApi):
    message = op(GetMessage)


async def main() -> None:
    site = GuideSite()
    with guide_origin(site) as base_url:
        # docs:integration-curl-cffi:start
        # examples/docs/integration_curl_cffi.py
        async with AsyncClient.curl_cffi(
            base_url=base_url,
            impersonate="chrome",
        ) as client:
            message = await MessageApi(client).message()
        # docs:integration-curl-cffi:end
    print(f"message: {message.id} {message.subject}")
    print(f"impersonation: {client.profile.impersonation}")


if __name__ == "__main__":
    asyncio.run(main())
