"""Parse a response with the built-in dataclass model adapter."""

from __future__ import annotations

from dataclasses import dataclass

from eazy_sdk import Http, HttpOperation, SyncApi, op

from .guide_site import GuideSite, guide_client


# docs:integration-dataclass-model:start
# examples/docs/integration_dataclass_model.py
@dataclass(frozen=True, slots=True)
class Message:
    id: int
    subject: str


@dataclass(frozen=True, slots=True)
class GetMessage(HttpOperation[Message]):
    __http__ = Http.get("/messages/42")


class MessagesApi(SyncApi):
    get = op(GetMessage)
# docs:integration-dataclass-model:end


def main() -> None:
    with guide_client(GuideSite()) as client:
        message = MessagesApi(client).get()
    print(f"dataclass: {message.id} {message.subject}")


if __name__ == "__main__":
    main()
