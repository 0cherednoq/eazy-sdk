"""Run the assembled HTTP mail SDK against the teaching site."""

from __future__ import annotations

import asyncio

from eazy_sdk import Identity

from examples.mail.http._client import async_mail_client
from examples.mail.http.send import MAIL_BEARER, NewMessage, signing_key
from examples.mail.sdk import MailSdk
from examples.mail.site import MailSite


# region docs: http-sdk-flow
# examples/mail/http/assembly.py
async def use_http_sdk() -> tuple[str, list[int], int]:
    site = MailSite()
    identity = Identity(
        auth=(MAIL_BEARER.static("session-1"),),
        key_provider=signing_key,
    )
    async with async_mail_client(site) as client:
        sdk = MailSdk(client, identity=identity)
        user = await sdk.account.current()
        page = await sdk.messages.page(offset=0, limit=2)
        sent = await sdk.messages.send(
            body=NewMessage(
                recipient="grace@mail.example",
                subject="Собранный SDK",
                body="Обе HTTP-группы работают.",
            )
        )
    return user.email, [message.id for message in page.items], sent.message_id or 0
# endregion docs: http-sdk-flow


async def run() -> None:
    email, messages, sent = await use_http_sdk()
    print("transport: httpx")
    print("routers: account, messages")
    print(f"user: {email}")
    print(f"message ids: {messages}")
    print(f"encrypted send: {sent}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
