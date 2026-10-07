"""Exercise a public SDK method against a deterministic local site."""

from dataclasses import dataclass

from pydantic import BaseModel

from eazy_sdk import Http, HttpOperation, Path, SyncApi, op
from examples.docs.guide_site import GuideSite, guide_client


class Message(BaseModel):
    id: int
    subject: str


# region docs: testing
# examples/docs/testing.py
@dataclass(frozen=True, slots=True, kw_only=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/{message_id}")
    message_id: Path[int]


class MessagesApi(SyncApi):
    get = op(GetMessage)


def main() -> None:
    site = GuideSite()
    with guide_client(site) as client:
        message = MessagesApi(client).get(message_id=42)
    print(f"message: {message.id} {message.subject}")
    print(f"observed: {site.calls[0]}")
# endregion docs: testing


if __name__ == "__main__":
    main()
