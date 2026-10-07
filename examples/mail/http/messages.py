"""Read the same mail feed with offset, cursor and next-URL pagination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel

from eazy_sdk import UNSET, Http, HttpOperation, Identity, Omittable, Query, SyncApi, op
from eazy_sdk.pagination import Pages
from eazy_sdk.response import Const, Json, Payload

from examples.mail.http._client import mail_client
from examples.mail.http.send import MAIL_BEARER


class Message(BaseModel):
    id: int
    sender: str
    recipient: str
    subject: str


class MessagePage(BaseModel):
    items: list[Message]
    total: int
    next_offset: int | None
    next_cursor: str | None
    next_url: str | None


class MessagePageEnvelope(BaseModel):
    status: Annotated[str, Const("ok")]
    body: Payload[MessagePage]


# region docs: http-message-pages
# examples/mail/http/messages.py
@dataclass(frozen=True, slots=True, kw_only=True)
class ListByOffset(HttpOperation[MessagePage]):
    __http__ = Http.get(
        "/api/v1/threads/status/smart",
        success={200: Json(MessagePageEnvelope)},
        security=MAIL_BEARER,
    )
    __pages__ = Pages.offset(
        MessagePage,
        offset="offset",
        limit="limit",
        items=lambda page: page.items,
        total=lambda page: page.total,
    )

    offset: Query[int] = 0
    limit: Query[int] = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class ListByCursor(HttpOperation[MessagePage]):
    __http__ = Http.get(
        "/api/v1/threads/status/smart",
        success={200: Json(MessagePageEnvelope)},
        security=MAIL_BEARER,
    )
    __pages__ = Pages.cursor(
        MessagePage,
        cursor="cursor",
        items=lambda page: page.items,
        next_cursor=lambda page: page.next_cursor,
    )

    cursor: Query[Omittable[str]] = UNSET
    limit: Query[int] = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class ListByNextUrl(HttpOperation[MessagePage]):
    __http__ = Http.get(
        "/api/v1/threads/status/smart",
        success={200: Json(MessagePageEnvelope)},
        security=MAIL_BEARER,
    )
    __pages__ = Pages.next_url(
        MessagePage,
        items=lambda page: page.items,
        next_url=lambda page: page.next_url,
    )

    offset: Query[int] = 0
    limit: Query[int] = 2


class MessagesApi(SyncApi):
    base_url = "https://mail.example"

    by_offset = op(ListByOffset)
    by_cursor = op(ListByCursor)
    by_next_url = op(ListByNextUrl)
# endregion docs: http-message-pages


# region docs: http-message-flow
# examples/mail/http/messages.py
def read_with_three_strategies() -> tuple[list[int], list[int], list[int]]:
    identity = Identity(auth=(MAIL_BEARER.static("session-1"),))
    with mail_client() as client:
        api = MessagesApi(client, identity=identity)
        offset = [message.id for message in api.by_offset.items()]
        cursor = [message.id for message in api.by_cursor.items()]
        next_url = [message.id for message in api.by_next_url.items()]
    return offset, cursor, next_url
# endregion docs: http-message-flow


def main() -> None:
    offset, cursor, next_url = read_with_three_strategies()
    print(f"offset: {offset}")
    print(f"cursor: {cursor}")
    print(f"next URL: {next_url}")


if __name__ == "__main__":
    main()
