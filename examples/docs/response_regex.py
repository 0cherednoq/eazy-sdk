"""Read a value out of an inline script, where no markup addresses it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk_html import CSS, Regex
from pydantic import BaseModel

from eazy_sdk import Cookie, Http, HttpOperation, SyncApi, op
from eazy_sdk.response import Const
from examples.docs.redirect_site import SESSION_ID, redirect_client


# docs:example:start
# examples/docs/response_regex.py
class MailPage(BaseModel):
    title: Annotated[str, CSS("title::text"), Const("Mail")]
    token: Annotated[str, Regex(r'"account":\{"token":"([^"]+)"')]
    email: Annotated[str, Regex(r'"account":\{[^}]*"email":"([^"]+)"')]
    every_token: Annotated[list[str], Regex(r'"token":"([^"]+)"')]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenInbox(HttpOperation[MailPage]):
    __http__ = Http.get("/inbox/")

    sid: Cookie[str]


class MailPages(SyncApi):
    inbox = op(OpenInbox)


# docs:example:end


def main() -> None:
    with redirect_client() as client:
        page = MailPages(client).inbox(sid=SESSION_ID)

    print(f"title: {page.title}")
    print(f"token: {page.token}")
    print(f"email: {page.email}")
    print(f"every token: {', '.join(page.every_token)}")


if __name__ == "__main__":
    main()
