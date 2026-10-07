"""Extract a typed model from an HTML response."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from eazy_sdk_html import CSS

from eazy_sdk import Http, HttpOperation, SyncApi, op

from .guide_site import GuideSite, guide_client


# docs:integration-html-document:start
# examples/docs/integration_html_document.py
@dataclass(frozen=True, slots=True)
class InboxPage:
    title: Annotated[str, CSS("h1::text")]
    subject: Annotated[str, CSS("article .subject::text")]


@dataclass(frozen=True, slots=True)
class GetInbox(HttpOperation[InboxPage]):
    __http__ = Http.get("/catalogue")


class InboxApi(SyncApi):
    get = op(GetInbox)
# docs:integration-html-document:end


def main() -> None:
    with guide_client(GuideSite()) as client:
        page = InboxApi(client).get()
    print(f"html: {page.title} — {page.subject}")


if __name__ == "__main__":
    main()
