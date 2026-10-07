"""Traverse numbered pages declared on an operation."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Query, SyncApi, op
from eazy_sdk.pagination import Pages
from examples.docs.guide_site import GuideSite, guide_client


class DocumentPage(BaseModel):
    items: list[str]
    pages_count: int


# region docs: pagination
# examples/docs/pagination.py
@dataclass(frozen=True, slots=True, kw_only=True)
class ListDocuments(HttpOperation[DocumentPage]):
    __http__ = Http.get("/documents")
    __pages__ = Pages.numbered(
        DocumentPage,
        page="page",
        size="per_page",
        items=lambda result: result.items,
        total_pages=lambda result: result.pages_count,
    )

    page: Query[int] = 1
    per_page: Query[int] = 2


class DocumentsApi(SyncApi):
    list = op(ListDocuments)


def main() -> None:
    with guide_client(GuideSite()) as client:
        documents = DocumentsApi(client)
        for number, page in enumerate(documents.list.pages(), start=1):
            print(f"page {number}: {', '.join(page.items)}")
# endregion docs: pagination


if __name__ == "__main__":
    main()
