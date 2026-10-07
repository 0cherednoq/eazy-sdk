"""Parse a response with the built-in msgspec model adapter."""

from __future__ import annotations

from dataclasses import dataclass

import msgspec

from eazy_sdk import Http, HttpOperation, SyncApi, op

from .guide_site import GuideSite, guide_client


# docs:integration-msgspec-model:start
# examples/docs/integration_msgspec_model.py
class Message(msgspec.Struct, frozen=True, rename={"message_id": "id"}):
    message_id: int
    subject: str


@dataclass(frozen=True, slots=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/42")


class MessagesApi(SyncApi):
    get = op(GetMessage)
# docs:integration-msgspec-model:end


def main() -> None:
    with guide_client(GuideSite()) as client:
        message = MessagesApi(client).get()
    print(f"msgspec: {message.message_id} {message.subject}")


if __name__ == "__main__":
    main()
